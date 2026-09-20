"""
SteamDT 真实数据采集器
======================
对接 SteamDT 开放平台（https://open.steamdt.com），拉取真实 CS2 饰品市场数据。

核心接口：
  1. POST /open/cs2/item/v1/kline       单品K线（365天 OHLC）      120次/分钟
  2. GET  /open/cs2/v1/price/single     各平台在售价/挂单量/求购量   60次/分钟
  3. GET  /open/cs2/broad/v1/index      大盘指数
  4. GET  /open/cs2/v1/base             饰品基础信息（每日仅1次，慎用）

数据写入 data/prices.csv，列结构：
  skin_name, date, price, volume, listings

  说明：SteamDT 不提供历史成交量，因此
    - price    : 真实收盘价（来自K线）
    - volume   : 历史K线无成交量数据，写 0
    - listings : 真实挂单量（仅"今天"这一行有值，其余为 0）
                 每天运行一次即可逐步积累挂单量历史
"""

import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Windows 编码修复（安全包装）
if sys.platform == "win32":
    import io
    try:
        if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if not isinstance(sys.stderr, io.TextIOWrapper) or sys.stderr.encoding != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (ValueError, AttributeError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd

from config import settings


class SteamDTError(Exception):
    """SteamDT 接口业务错误"""
    pass


class SteamDTCollector:
    """SteamDT 开放平台数据采集器"""

    def __init__(self, api_key: Optional[str] = None):
        cfg = settings.STEAMDT_CONFIG
        self.cfg = cfg
        self.base_url = cfg["base_url"].rstrip("/")
        self.timeout = cfg.get("timeout", 20)
        self.interval = cfg.get("request_interval", 1.0)
        self.data_file = settings.DATA_FILE

        self.api_key = (
            api_key
            or os.environ.get(cfg.get("env_key", "STEAMDT_API_KEY"), "").strip()
            or cfg.get("api_key", "").strip()
        )
        if not self.api_key:
            raise SteamDTError(
                "未配置 SteamDT API Key。请设置环境变量：\n"
                "  Windows (cmd):  set STEAMDT_API_KEY=你的key\n"
                "  或填在 config/settings.py 的 STEAMDT_CONFIG['api_key']"
            )

        self.session = requests.Session()
        self._last_request = 0.0
        self.stats = {"kline_ok": 0, "kline_fail": 0, "price_ok": 0, "price_fail": 0}

    # ------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request = time.time()

    def _request(self, method: str, path: str, *, params=None, json=None) -> Dict:
        """统一请求入口，带限速和错误处理"""
        self._rate_limit()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = self.session.request(
            method, url, headers=headers, params=params, json=json, timeout=self.timeout
        )
        if resp.status_code == 405:
            raise SteamDTError(f"请求方法错误: {method} {path}")
        if resp.status_code == 404:
            raise SteamDTError(f"接口不存在: {path}")
        resp.raise_for_status()

        data = resp.json()
        if not data.get("success"):
            code = data.get("errorCode")
            msg = data.get("errorMsg") or data.get("errorCodeStr") or "未知错误"
            raise SteamDTError(f"接口返回失败 [{code}] {msg}")
        return data

    # ------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------

    def fetch_kline(self, market_hash_name: str, ktype: int = 2) -> List[List]:
        """
        获取单品K线（价格历史）。

        参数:
            market_hash_name: 必须带磨损后缀，如 "AK-47 | Redline (Field-Tested)"
            ktype: 1=时K, 2=日K, 3=周K

        返回: [[时间戳, 开, 收, 高, 低], ...]
        """
        data = self._request(
            "POST", "/open/cs2/item/v1/kline",
            json={"marketHashName": market_hash_name, "type": ktype},
        )
        return data.get("data") or []

    def fetch_price(self, market_hash_name: str) -> Dict:
        """
        获取单品各平台价格 / 挂单量 / 求购量。

        返回标准化后的字典：
            {
              "platform": "BUFF",
              "sell_price": 178.0,       # 在售最低价
              "sell_count": 8350,        # 在售数量（挂单量）
              "bidding_price": 173.0,    # 求购最高价
              "bidding_count": 93,       # 求购数量
              "all_platforms": {...}     # 各平台原始数据
            }
        """
        data = self._request(
            "GET", "/open/cs2/v1/price/single",
            params={"marketHashName": market_hash_name},
        )
        platforms = data.get("data") or []

        if isinstance(platforms, dict):
            platforms = [platforms]

        by_platform = {}
        for p in platforms:
            name = (p.get("platform") or "").upper()
            if name:
                by_platform[name] = p

        # 按偏好顺序挑一个有效平台
        chosen = None
        for pref in self.cfg.get("preferred_platforms", ["BUFF"]):
            cand = by_platform.get(pref)
            if cand and (cand.get("sellPrice") or cand.get("sellCount")):
                chosen = cand
                break
        if chosen is None:
            # 回退：取任意有数据的平台
            for cand in by_platform.values():
                if cand.get("sellPrice") or cand.get("sellCount"):
                    chosen = cand
                    break

        if chosen is None:
            return {}

        return {
            "platform": chosen.get("platform"),
            "sell_price": self._safe_float(chosen.get("sellPrice")),
            "sell_count": self._safe_int(chosen.get("sellCount")),
            "bidding_price": self._safe_float(chosen.get("biddingPrice")),
            "bidding_count": self._safe_int(chosen.get("biddingCount")),
            "all_platforms": {
                k: {
                    "sell_price": self._safe_float(v.get("sellPrice")),
                    "sell_count": self._safe_int(v.get("sellCount")),
                    "bidding_count": self._safe_int(v.get("biddingCount")),
                }
                for k, v in by_platform.items()
            },
        }

    def fetch_broad_index(self) -> Dict:
        """获取 CS2 大盘最新指数"""
        data = self._request("GET", "/open/cs2/broad/v1/index")
        return data.get("data") or {}

    # ------------------------------------------------------------
    # 采集主流程
    # ------------------------------------------------------------

    def collect(self, skin_names: Optional[List[str]] = None, days: int = 365) -> Tuple[int, int]:
        """
        采集真实数据并写入 CSV。

        返回: (成功皮肤数, 失败皮肤数)
        """
        if skin_names is None:
            skin_names = settings.STEAMDT_SKINS

        print("=" * 64)
        print("📡 SteamDT 真实市场数据采集")
        print("=" * 64)
        print(f"待采集: {len(skin_names)} 款皮肤")
        print(f"K线周期: 日K，最多 {days} 天")
        print(f"限速: 每请求间隔 {self.interval} 秒")
        print()

        today = datetime.now().strftime("%Y-%m-%d")
        records: List[Dict] = []
        ok, fail = 0, 0

        for i, name in enumerate(skin_names, 1):
            print(f"[{i}/{len(skin_names)}] {name}")

            # 1) 拉K线 → 历史价格
            try:
                kline = self.fetch_kline(name)
                self.stats["kline_ok"] += 1
            except SteamDTError as e:
                print(f"    ❌ K线失败: {e}")
                self.stats["kline_fail"] += 1
                fail += 1
                continue
            except Exception as e:
                print(f"    ❌ K线异常: {e}")
                self.stats["kline_fail"] += 1
                fail += 1
                continue

            if not kline:
                print("    ⚠️ 无K线数据（名字或磨损后缀可能不对）")
                fail += 1
                continue

            kline = kline[-days:] if len(kline) > days else kline
            for row in kline:
                try:
                    ts = int(row[0])
                    close = self._safe_float(row[2])   # [时间, 开, 收, 高, 低]
                    date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    if close <= 0:
                        continue
                    records.append({
                        "skin_name": name,
                        "date": date_str,
                        "price": round(close, 2),
                        "volume": 0,
                        "listings": 0,
                    })
                except (IndexError, ValueError, TypeError):
                    continue

            # 2) 拉当前挂单量 → 只填到"今天"这一行
            try:
                price_info = self.fetch_price(name)
                self.stats["price_ok"] += 1
                if price_info and price_info.get("sell_count"):
                    # 用挂单量更新今天那条记录
                    for r in reversed(records):
                        if r["skin_name"] == name and r["date"] == today:
                            r["listings"] = price_info["sell_count"]
                            r["price"] = price_info.get("sell_price") or r["price"]
                            break
                    else:
                        # 今天还没有K线，则补一条
                        records.append({
                            "skin_name": name,
                            "date": today,
                            "price": round(price_info.get("sell_price") or 0, 2),
                            "volume": 0,
                            "listings": price_info["sell_count"],
                        })
                    print(f"    ✅ {len(kline)} 天K线 | 挂单量 {price_info['sell_count']}"
                          f" | 在售 ¥{price_info.get('sell_price')}"
                          f" | 求购 {price_info.get('bidding_count')}"
                          f" @{price_info.get('platform')}")
                else:
                    print(f"    ✅ {len(kline)} 天K线 | ⚠️ 无挂单量数据")
            except Exception as e:
                self.stats["price_fail"] += 1
                print(f"    ✅ {len(kline)} 天K线 | ⚠️ 挂单量失败: {str(e)[:60]}")

            ok += 1

        if records:
            self._write_csv(records)
            print(f"\n💾 已写入 {len(records)} 条记录 → {self.data_file}")

        print("\n" + "=" * 64)
        print(f"📋 采集完成: {ok} 成功, {fail} 失败")
        print(f"   K线: {self.stats['kline_ok']} 成功 / {self.stats['kline_fail']} 失败")
        print(f"   挂单量: {self.stats['price_ok']} 成功 / {self.stats['price_fail']} 失败")
        print("=" * 64)
        return ok, fail

    # ------------------------------------------------------------
    # 存储
    # ------------------------------------------------------------

    def _write_csv(self, records: List[Dict]):
        """合并写入 CSV（同 skin_name+date 覆盖，保留其它历史数据）"""
        df_new = pd.DataFrame(records)
        cols = ["skin_name", "date", "price", "volume", "listings"]
        df_new = df_new[cols]

        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)

        if os.path.exists(self.data_file):
            df_old = pd.read_csv(self.data_file)
            # 兼容旧文件没有 listings 列的情况
            if "listings" not in df_old.columns:
                df_old["listings"] = 0
            df_old = df_old[cols]
            df_all = pd.concat([df_old, df_new], ignore_index=True)
            df_all = df_all.drop_duplicates(subset=["skin_name", "date"], keep="last")
        else:
            df_all = df_new

        df_all = df_all.sort_values(["skin_name", "date"]).reset_index(drop=True)
        df_all.to_csv(self.data_file, index=False, encoding="utf-8-sig")

    # ------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------

    @staticmethod
    def _safe_float(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _safe_int(v) -> int:
        if v is None:
            return 0
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0


def main():
    """命令行入口：python data/steamdt_collector.py"""
    try:
        collector = SteamDTCollector()
    except SteamDTError as e:
        print(f"❌ {e}")
        return

    # 先看一眼大盘
    try:
        idx = collector.fetch_broad_index()
        if idx:
            print(f"\n📈 CS2 大盘指数: {idx.get('broadMarketIndex')} "
                  f"(较昨日 {idx.get('diffYesterday')}, {idx.get('diffYesterdayRatio')}%)\n")
    except Exception as e:
        print(f"⚠️ 大盘指数获取失败: {e}\n")

    collector.collect()


if __name__ == "__main__":
    main()
