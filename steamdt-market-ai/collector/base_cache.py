"""
全量饰品基础信息缓存
====================
SteamDT 的 /open/cs2/v1/base 接口**每天只能调用 1 次**，
一旦浪费掉，当天就再也拿不到全市场列表。因此必须落盘缓存。

缓存策略：
  1. 优先读本地缓存（data/cache/steam_items_base.json）
  2. 缓存存在且有效 → 直接返回，不调 API
  3. 需要刷新时调 API：
       - 成功            → 写入缓存
       - 4005（超限）     → 回退到旧缓存（若存在）
       - 其他错误         → 回退到旧缓存（若存在），否则报错
  4. 记录抓取时间，方便判断缓存新鲜度

用法：
    from collector.base_cache import BaseCache
    cache = BaseCache()
    items = cache.get_items()          # 优先缓存
    items = cache.refresh()            # 强制从 API 刷新（消耗每日额度）
    print(cache.info())                # 查看缓存状态
"""

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BASE_CACHE_FILE, ensure_dirs
from collector.client import SteamDTClient, SteamDTError


class BaseCache:
    """全量饰品列表的本地缓存管理器"""

    def __init__(self, cache_file: str = BASE_CACHE_FILE):
        self.cache_file = cache_file
        ensure_dirs()

    # ------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------

    def load(self) -> Optional[Dict[str, Any]]:
        """读取本地缓存，无效则返回 None"""
        if not os.path.exists(self.cache_file):
            return None
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("items"), list) or len(data["items"]) == 0:
            return None
        return data

    def get_items(self, allow_fetch: bool = True) -> List[Dict]:
        """
        获取全量饰品列表（优先缓存）。

        参数:
            allow_fetch: 缓存不存在时是否允许调用 API（会消耗每日额度）
        """
        cached = self.load()
        if cached is not None:
            return cached["items"]

        if not allow_fetch:
            raise FileNotFoundError(
                f"缓存不存在: {self.cache_file}\n"
                f"请先执行一次 refresh()（会消耗 SteamDT base 接口的每日 1 次额度）"
            )

        return self.refresh()

    # ------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------

    def refresh(self) -> List[Dict]:
        """
        强制从 API 刷新（⚠️ 消耗 base 接口每日 1 次额度）。

        失败时自动回退到旧缓存；无旧缓存则抛出异常。
        """
        print("📡 正在调用 SteamDT base 接口（每日限 1 次）...")

        try:
            with SteamDTClient() as client:
                items = client.get_base_items()
        except SteamDTError as e:
            print(f"   ❌ API 调用失败: {e}")
            cached = self.load()
            if cached is not None:
                age = self._age_hours(cached.get("fetched_at"))
                print(f"   ↩️  回退到旧缓存（{len(cached['items'])} 条，"
                      f"{'未知时间' if age is None else f'{age:.1f} 小时前'}抓取）")
                return cached["items"]
            raise

        if not items:
            print("   ⚠️ API 返回空列表")
            cached = self.load()
            if cached is not None:
                print(f"   ↩️  回退到旧缓存（{len(cached['items'])} 条）")
                return cached["items"]
            raise SteamDTError("base 接口返回空数据且无可用缓存")

        self._save(items)
        print(f"   ✅ 已刷新并缓存 {len(items)} 条 → {self.cache_file}")
        return items

    def _save(self, items: List[Dict]):
        """写入缓存，附带抓取时间等元信息"""
        payload = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items),
            "items": items,
        }
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------

    @staticmethod
    def _age_hours(fetched_at: Optional[str]) -> Optional[float]:
        if not fetched_at:
            return None
        try:
            dt = datetime.fromisoformat(fetched_at)
            return (datetime.now() - dt).total_seconds() / 3600
        except (ValueError, TypeError):
            return None

    def info(self) -> Dict[str, Any]:
        """返回缓存状态，便于排查"""
        cached = self.load()
        if cached is None:
            return {"exists": False, "path": self.cache_file}

        age = self._age_hours(cached.get("fetched_at"))
        return {
            "exists": True,
            "path": self.cache_file,
            "count": cached.get("count"),
            "fetched_at": cached.get("fetched_at"),
            "age_hours": round(age, 2) if age is not None else None,
            "is_fresh": age is not None and age < 24,
        }

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """
        在缓存的全量饰品中智能搜索（支持中英文混合、口语昵称、武器别名）。

            cache.search("ak 红线")   → AK-47 | Redline
            cache.search("awp龙狙")   → AWP | Dragon Lore
            cache.search("沙鹰")      → Desert Eagle 系列

        匹配逻辑见 search/matcher.py。
        """
        from search.matcher import search as smart_search
        return smart_search(query, self.get_items(), limit=limit)


if __name__ == "__main__":
    cache = BaseCache()
    print("缓存状态:", json.dumps(cache.info(), ensure_ascii=False, indent=2))
