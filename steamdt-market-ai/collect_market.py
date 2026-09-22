"""
全市场数据采集
==============
把 8969 个饰品的真实行情采集入库。

用法:
    python collect_market.py --sync-items           # 同步饰品主表
    python collect_market.py --prices               # 采集全市场价格（约 90 分钟，可中断续跑）
    python collect_market.py --klines --top 500     # 给挂单量最高的 500 款拉 K 线
    python collect_market.py --status               # 查看采集进度
    python collect_market.py --all                  # 依次执行上面三步

设计要点:
  1. **可中断续跑**：状态存 data/collect_state.json，中断后重跑自动跳过已完成批次
  2. **限速安全**：price/batch 官方限制每分钟 1 次，代码里留了 1 秒余量
  3. **批量写库**：每批 100 个饰品只用一次数据库事务，避免 N 次连接开销
  4. **失败不中断**：单批失败会记录并继续，最后汇总报告
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import COLLECT, get_api_key
from collector.client import SteamDTClient, SteamDTError
from collector.item_catalog import ItemCatalog
from db.connection import get_conn, query, query_one, table_counts


# ============================================================
# 采集状态（支持中断续跑）
# ============================================================

class CollectState:
    """记录采集进度，中断后可续跑"""

    def __init__(self, path: str = None):
        self.path = path or COLLECT["state_file"]
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                # 跨天自动重置（每天重新采集）
                if d.get("date") == datetime.now().strftime("%Y-%m-%d"):
                    return d
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "prices": {"done": [], "failed": []},
            "klines": {"done": [], "failed": []},
        }

    def save(self):
        """
        原子写入。

        直接 'w' 覆盖写有风险：若在截断与写入之间进程被杀，
        文件会变成空文件/半截 JSON，下次读取被判为无效 → 丢掉全部进度重来。
        改用「写临时文件 + 原子替换」。
        """
        self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)      # 原子操作

    def set_names_signature(self, names: List[str]):
        """
        记录本次采集的名单指纹。

        续跑进度是按「批次序号」存的，而批次内容由名单切分而来。
        如果名单变了（比如目录重建、新增皮肤），同一个序号会指向不同的饰品，
        导致旧进度错误地跳过新饰品。用指纹检测这种错位。
        """
        import hashlib
        sig = hashlib.md5("\n".join(names).encode("utf-8")).hexdigest()[:16]
        old = self.data["prices"].get("names_sig")

        if old and old != sig:
            print(f"⚠️  检测到采集名单已变化（{old} → {sig}）")
            print("    批次序号对应的饰品已错位，本次将重置价格采集进度")
            self.data["prices"] = {"done": [], "failed": [], "names_sig": sig}
        else:
            self.data["prices"]["names_sig"] = sig
        return sig

    # --- prices ---
    def price_done(self, idx: int):
        if idx not in self.data["prices"]["done"]:
            self.data["prices"]["done"].append(idx)

    def price_failed(self, idx: int, reason: str = ""):
        self.data["prices"]["failed"].append({"idx": idx, "reason": reason[:200]})

    def price_is_done(self, idx: int) -> bool:
        return idx in self.data["prices"]["done"]

    # --- klines ---
    def kline_done(self, mhn: str):
        if mhn not in self.data["klines"]["done"]:
            self.data["klines"]["done"].append(mhn)

    def kline_is_done(self, mhn: str) -> bool:
        return mhn in self.data["klines"]["done"]


# ============================================================
# 采集器
# ============================================================

class MarketCollector:

    def __init__(self):
        self.client = SteamDTClient()
        self.catalog = ItemCatalog()
        self.state = CollectState()
        self.today = datetime.now().strftime("%Y-%m-%d")

    # ------------------------------------------------------------
    # 1. 同步饰品主表
    # ------------------------------------------------------------

    def sync_items(self) -> int:
        """把全市场目录写入 items 表"""
        print("=" * 64)
        print("📋 同步饰品主表")
        print("=" * 64)

        items = self.catalog.get_items()
        print(f"目录共 {len(items)} 个 market_hash_name")

        rows = [
            (
                it["market_hash_name"],
                it.get("name_cn", ""),
                it.get("weapon", ""),
                it.get("wear", ""),
            )
            for it in items
        ]

        sql = """
            INSERT INTO items (market_hash_name, name_cn, weapon, wear)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name_cn = VALUES(name_cn),
                weapon  = VALUES(weapon),
                wear    = VALUES(wear)
        """

        # 分批写入，避免单次语句过大
        BATCH = 500
        total = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for i in range(0, len(rows), BATCH):
                    chunk = rows[i:i + BATCH]
                    cur.executemany(sql, chunk)
                    total += len(chunk)
                    print(f"  写入 {total}/{len(rows)} ...", end="\r")

        print()
        print(f"✅ items 表同步完成，共 {total} 条")
        return total

    # ------------------------------------------------------------
    # 2. 批量采集价格 / 挂单量
    # ------------------------------------------------------------

    def _load_item_id_map(self) -> Dict[str, int]:
        """加载 market_hash_name → item_id 映射"""
        rows = query("SELECT item_id, market_hash_name FROM items")
        return {r["market_hash_name"]: r["item_id"] for r in rows}

    def collect_prices(self, max_batches: Optional[int] = None) -> Tuple[int, int]:
        """
        批量采集全市场价格（price/batch，每分钟 1 次）。

        返回: (成功批次数, 失败批次数)
        """
        print("=" * 64)
        print("📡 全市场价格采集")
        print("=" * 64)

        items = self.catalog.get_items()
        item_ids = self._load_item_id_map()

        if not item_ids:
            print("❌ items 表为空。请先执行: python collect_market.py --sync-items")
            return 0, 0

        # 只采集能在 items 表里找到的
        names = [it["market_hash_name"] for it in items if it["market_hash_name"] in item_ids]
        print(f"待采集: {len(names)} 个（items 表中共 {len(item_ids)} 条）")

        size = COLLECT["batch_size"]
        batches = [names[i:i + size] for i in range(0, len(names), size)]
        total_batches = len(batches)

        # 校验名单是否与上次一致（防止批次序号错位）
        self.state.set_names_signature(names)
        self.state.save()

        pending = [i for i in range(total_batches) if not self.state.price_is_done(i)]

        print(f"总批次: {total_batches}（每批 {size} 个）")
        print(f"已完成: {total_batches - len(pending)} 批")
        print(f"待采集: {len(pending)} 批")

        if not pending:
            print("✅ 今天已全部采集完成")
            return 0, 0

        interval = COLLECT["batch_interval"]
        eta_min = len(pending) * interval / 60
        print(f"限速: 每批间隔 {interval} 秒 → 预计耗时 {eta_min:.0f} 分钟")
        print(f"（可随时 Ctrl+C 中断，下次运行自动续跑）")
        print()

        if max_batches:
            pending = pending[:max_batches]
            print(f"⚠️  已限制本次只跑 {max_batches} 批（调试模式）")
            print()

        ok, fail = 0, 0
        consecutive_errors = 0
        max_consecutive = 3          # 连续失败阈值，超过则中止（避免空烧额度）
        t_start = time.time()

        for n, idx in enumerate(pending, 1):
            batch = batches[idx]
            elapsed = time.time() - t_start
            eta = (len(pending) - n + 1) * interval / 60

            print(f"[{n}/{len(pending)}] 批次 {idx + 1}/{total_batches} "
                  f"| 已用 {elapsed/60:.1f}分 | 剩余约 {eta:.0f}分", flush=True)

            try:
                data = self.client.get_price_batch(batch)

                # ---- 关键：校验返回值，避免"API成功但没写进去"却标记完成 ----
                if not data:
                    raise SteamDTError(f"接口返回空数据（期望 {len(batch)} 个），本批不标记完成")

                rows = self._parse_listings(data, item_ids)

                if not rows:
                    raise SteamDTError(
                        f"返回 {len(data)} 条但解析出 0 行挂单记录"
                        f"（可能字段名变更或饰品不在 items 表），本批不标记完成"
                    )

                self._bulk_save_listings(rows)

                # 二次校验：回查数据库，确认真的写进去了
                batch_item_ids = [
                    item_ids[e["marketHashName"]]
                    for e in data
                    if isinstance(e, dict) and e.get("marketHashName") in item_ids
                ]
                written = self._count_today_listings(batch_item_ids)
                if written == 0:
                    raise SteamDTError("写入后回查为 0 行，本批不标记完成")

                print(f"    ✅ {len(data)} 个返回，写入 {len(rows)} 条挂单记录"
                      f"（库内可见 {written} 条）")

                self.state.price_done(idx)
                ok += 1
                consecutive_errors = 0

            except SteamDTError as e:
                print(f"    ❌ 失败: {e}")
                self.state.price_failed(idx, str(e))
                fail += 1
                consecutive_errors += 1
            except Exception as e:
                print(f"    ❌ 异常: {str(e)[:120]}")
                self.state.price_failed(idx, str(e))
                fail += 1
                consecutive_errors += 1

            self.state.save()

            # 连续失败 → 中止，避免在故障状态下继续烧 API 额度
            if consecutive_errors >= max_consecutive:
                print()
                print("=" * 64)
                print(f"⛔ 连续 {consecutive_errors} 批失败，已中止采集。")
                print("   常见原因：MySQL 未启动、API Key 失效、网络中断。")
                print("   排查后重新运行即可续跑（进度已保存）。")
                print("=" * 64)
                break

            # 最后一批不用等
            if n < len(pending):
                time.sleep(interval)

        print()
        print("=" * 64)
        print(f"📋 采集完成: {ok} 成功, {fail} 失败")
        print(f"⏱️  总耗时: {(time.time() - t_start)/60:.1f} 分钟")
        print("=" * 64)
        return ok, fail

    def _parse_listings(self, data: List[Dict], item_ids: Dict[str, int]) -> List[Tuple]:
        """把 price/batch 的响应解析成待插入的行"""
        rows = []

        for entry in data:
            if not isinstance(entry, dict):
                continue

            mhn = entry.get("marketHashName", "")
            item_id = item_ids.get(mhn)
            if not item_id:
                continue

            for p in (entry.get("dataList") or []):
                if not isinstance(p, dict):
                    continue

                platform = (p.get("platform") or "").upper()
                sell_count = self._safe_int(p.get("sellCount"))
                sell_price = self._safe_float(p.get("sellPrice"))
                bid_count = self._safe_int(p.get("biddingCount"))
                bid_price = self._safe_float(p.get("biddingPrice"))

                # 四个字段全空才跳过。
                # 注意：不能只看 sell_count/sell_price —— 有些品种无人挂单但有买单，
                # 只按在售判断会连带丢掉有价值的求购数据。
                if sell_count == 0 and sell_price == 0 and bid_count == 0 and bid_price == 0:
                    continue

                rows.append((
                    item_id,
                    self.today,
                    platform,
                    sell_price,
                    sell_count,
                    bid_price,
                    bid_count,
                ))

        return rows

    def _count_today_listings(self, item_ids: List[int]) -> int:
        """回查数据库中这些饰品今天的挂单记录数（用于校验写入是否成功）"""
        if not item_ids:
            return 0
        placeholders = ",".join(["%s"] * len(item_ids))
        row = query_one(
            f"SELECT COUNT(*) AS n FROM listings "
            f"WHERE snap_date = %s AND item_id IN ({placeholders})",
            tuple([self.today] + item_ids),
        )
        return row["n"] if row else 0

    def _bulk_save_listings(self, rows: List[Tuple]):
        """批量写入挂单量快照（单次事务）"""
        sql = """
            INSERT INTO listings
                (item_id, snap_date, platform, sell_price, sell_count, bid_price, bid_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                sell_price = VALUES(sell_price),
                sell_count = VALUES(sell_count),
                bid_price  = VALUES(bid_price),
                bid_count  = VALUES(bid_count)
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)

    # ------------------------------------------------------------
    # 3. 拉取 K 线（只给重点品种）
    # ------------------------------------------------------------

    def collect_klines(self, top_n: int = None) -> Tuple[int, int]:
        """
        给挂单量最高的 N 款饰品拉取历史 K 线。

        K 线限速 120 次/分钟，比 price/batch 快得多，所以只做重点品种。
        """
        top_n = top_n or COLLECT["kline_top_n"]

        print("=" * 64)
        print(f"📈 拉取 K 线（按流动性筛选 Top {top_n}）")
        print("=" * 64)

        # 按"昨天/今天最新的挂单量"排序筛选
        candidates = query("""
            SELECT i.item_id, i.market_hash_name, MAX(l.sell_count) AS sc
            FROM items i
            JOIN listings l ON l.item_id = i.item_id
            GROUP BY i.item_id, i.market_hash_name
            HAVING sc > 0
            ORDER BY sc DESC
            LIMIT %s
        """, (top_n,))

        if not candidates:
            print("❌ 没有候选品种。请先执行: python collect_market.py --prices")
            return 0, 0

        pending = [c for c in candidates if not self.state.kline_is_done(c["market_hash_name"])]
        print(f"候选 {len(candidates)} 个，待采集 {len(pending)} 个")
        print(f"限速 120 次/分钟 → 预计 {len(pending)/120:.0f} 分钟")
        print()

        ok, fail = 0, 0
        t_start = time.time()

        for n, c in enumerate(pending, 1):
            mhn = c["market_hash_name"]
            try:
                kline = self.client.get_kline(mhn)
                if kline:
                    rows = []
                    for r in kline:
                        try:
                            d = datetime.fromtimestamp(int(r[0])).strftime("%Y-%m-%d")
                            rows.append((d, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
                        except (IndexError, ValueError, TypeError):
                            continue
                    if rows:
                        self._bulk_save_prices(c["item_id"], rows)

                self.state.kline_done(mhn)
                ok += 1

                if n % 20 == 0 or n == len(pending):
                    elapsed = time.time() - t_start
                    print(f"  [{n}/{len(pending)}] 已用 {elapsed/60:.1f} 分钟 | 最新: {mhn[:40]}")

            except Exception as e:
                fail += 1
                print(f"  ❌ {mhn[:40]}: {str(e)[:60]}")

            if n % 50 == 0:
                self.state.save()

        self.state.save()
        print()
        print(f"✅ K 线采集完成: {ok} 成功, {fail} 失败，耗时 {(time.time()-t_start)/60:.1f} 分钟")
        return ok, fail

    def _bulk_save_prices(self, item_id: int, rows: List[Tuple]):
        sql = """
            INSERT INTO prices (item_id, trade_date, open, close, high, low)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open), close=VALUES(close),
                high=VALUES(high), low=VALUES(low)
        """
        data = [(item_id, d, o, c, h, l) for (d, o, c, h, l) in rows]
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, data)

    # ------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------

    def status(self):
        """显示采集进度"""
        counts = table_counts()
        s = self.state.data

        print("=" * 64)
        print("📊 采集进度")
        print("=" * 64)
        print(f"日期: {s.get('date')}")
        print()
        print("数据库:")
        for k, v in counts.items():
            print(f"  {k:<14} {v:>10,} 条")
        print()
        print("本次运行状态:")
        print(f"  价格批次已完成: {len(s['prices']['done'])}")
        print(f"  价格批次失败:   {len(s['prices']['failed'])}")
        print(f"  K线已完成:      {len(s['klines']['done'])}")
        print("=" * 64)

    @staticmethod
    def _safe_int(v) -> int:
        """
        安全转 int。

        注意必须捕获 OverflowError：接口返回科学计数法或超大值时会抛
        "cannot convert float infinity to integer"，若不捕获会让整个批次
        永久失败（失败批次不标记完成 → 重跑还是同样输入 → 死循环）。
        """
        try:
            f = float(v)
            if f != f or f in (float("inf"), float("-inf")):   # NaN / Inf
                return 0
            return int(f)
        except (ValueError, TypeError, OverflowError):
            return 0

    @staticmethod
    def _safe_float(v) -> float:
        try:
            f = float(v)
            if f != f or f in (float("inf"), float("-inf")):
                return 0.0
            return f
        except (ValueError, TypeError, OverflowError):
            return 0.0


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="SteamDT 全市场数据采集")
    parser.add_argument("--sync-items", action="store_true", help="同步饰品主表")
    parser.add_argument("--prices", action="store_true", help="采集全市场价格（约90分钟）")
    parser.add_argument("--klines", action="store_true", help="拉取重点品种 K 线")
    parser.add_argument("--top", type=int, default=None, help="K 线采集数量（默认 500）")
    parser.add_argument("--status", action="store_true", help="查看采集进度")
    parser.add_argument("--all", action="store_true", help="依次执行同步+价格+K线")
    parser.add_argument("--max-batches", type=int, default=None, help="限制批次数（调试用）")
    args = parser.parse_args()

    # 没给任何参数时显示帮助
    if not any([args.sync_items, args.prices, args.klines, args.status, args.all]):
        parser.print_help()
        return

    try:
        get_api_key()
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    collector = MarketCollector()

    try:
        if args.status:
            collector.status()
            return

        if args.all or args.sync_items:
            collector.sync_items()
            print()

        if args.all or args.prices:
            collector.collect_prices(max_batches=args.max_batches)
            print()

        if args.all or args.klines:
            collector.collect_klines(top_n=args.top)

    except KeyboardInterrupt:
        print("\n\n⚠️  已中断。进度已保存，重新运行将自动续跑。")
        collector.state.save()
    finally:
        collector.client.close()


if __name__ == "__main__":
    main()
