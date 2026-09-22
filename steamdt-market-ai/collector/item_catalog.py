"""
全市场饰品目录
==============
构建 CS2 全市场饰品的 market_hash_name 清单。

为什么需要这个？
    SteamDT 的 /open/cs2/v1/base（全量列表）**每天只能调用 1 次**，
    一旦用掉当天就没有了，会成为整个系统的单点瓶颈。

解决方案：双来源
    来源 A（主力）: 开源数据集 ByMykel/CSGO-API
        - 2126 款皮肤 × 各磨损档位 ≈ 9285 个 market_hash_name
        - 通过 jsDelivr CDN 获取（国内可直连），**不消耗任何 API 额度**
    来源 B（补充）: SteamDT base 接口
        - 官方权威数据，但每日仅 1 次
        - 两个来源合并可互相校准

market_hash_name 构造规则：
    普通武器:  {weapon} | {pattern} ({wear})          如 AK-47 | Redline (Field-Tested)
    匕首/手套: ★ {weapon} | {pattern} ({wear})        如 ★ Karambit | Doppler (Factory New)
                                                       ↑ Steam 上刀和手套带星号前缀
"""

import json
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from config import DATA_DIR, ensure_dirs
from collector.client import SteamDTClient, SteamDTError

CATALOG_FILE = os.path.join(DATA_DIR, "item_catalog.json")

# 开源数据集的 CDN（jsDelivr 国内可直连，按顺序回退）
DATASET_URLS = {
    "en": [
        "https://cdn.jsdelivr.net/gh/ByMykel/CSGO-API@main/public/api/en/skins.json",
        "https://fastly.jsdelivr.net/gh/ByMykel/CSGO-API@main/public/api/en/skins.json",
    ],
    "zh-CN": [
        "https://cdn.jsdelivr.net/gh/ByMykel/CSGO-API@main/public/api/zh-CN/skins.json",
        "https://fastly.jsdelivr.net/gh/ByMykel/CSGO-API@main/public/api/zh-CN/skins.json",
    ],
}

# 这些分类的饰品在 Steam 上带 ★ 前缀
STAR_CATEGORIES = {"Knives", "Gloves"}


class ItemCatalog:
    """全市场饰品目录管理"""

    def __init__(self, catalog_file: str = CATALOG_FILE):
        self.catalog_file = catalog_file
        ensure_dirs()

    # ------------------------------------------------------------
    # 来源 A：开源数据集
    # ------------------------------------------------------------

    @staticmethod
    def _download_dataset(lang: str) -> Optional[List[Dict]]:
        """从 CDN 下载数据集（多个源依次尝试）"""
        for url in DATASET_URLS.get(lang, []):
            try:
                print(f"   尝试 {url.split('/')[2]} ...", end=" ")
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"✅ {len(data)} 条")
                    return data
                print(f"HTTP {resp.status_code}")
            except Exception as e:
                print(f"失败 ({str(e)[:40]})")
        return None

    @staticmethod
    def build_market_hash_name(weapon: str, pattern: str, wear: str, category: str = "") -> str:
        """
        按 Steam 规则构造 market_hash_name。

        匕首和手套需要 ★ 前缀，这是最容易踩的坑。
        """
        base = f"{weapon} | {pattern} ({wear})"
        if category in STAR_CATEGORIES:
            return f"★ {base}"
        return base

    def build_from_dataset(self) -> List[Dict]:
        """从开源数据集构造全量列表（中英文合并）"""
        print("📦 从开源数据集构建饰品目录")

        en = self._download_dataset("en")
        if not en:
            raise RuntimeError("无法获取英文数据集（所有 CDN 均失败）")

        zh = self._download_dataset("zh-CN") or []

        # 按 id 建立中文名索引
        zh_map = {x.get("id"): x for x in zh if isinstance(x, dict)}

        items = []
        for x in en:
            if not isinstance(x, dict):
                continue

            weapon = (x.get("weapon") or {}).get("name", "")
            pattern = (x.get("pattern") or {}).get("name", "")
            category = (x.get("category") or {}).get("name", "")
            wears = x.get("wears") or []

            if not weapon or not pattern or not wears:
                continue

            zh_item = zh_map.get(x.get("id")) or {}
            zh_weapon = (zh_item.get("weapon") or {}).get("name", "")
            zh_pattern = (zh_item.get("pattern") or {}).get("name", "")
            zh_wear_map = {
                w.get("id"): w.get("name")
                for w in (zh_item.get("wears") or [])
                if isinstance(w, dict)
            }

            for w in wears:
                if not isinstance(w, dict):
                    continue
                wear = w.get("name", "")
                if not wear:
                    continue

                mhn = self.build_market_hash_name(weapon, pattern, wear, category)

                # 中文名
                zh_wear = zh_wear_map.get(w.get("id"), "")
                name_cn = ""
                if zh_weapon and zh_pattern and zh_wear:
                    name_cn = self.build_market_hash_name(zh_weapon, zh_pattern, zh_wear, category)

                items.append({
                    "market_hash_name": mhn,
                    "name_cn": name_cn or mhn,
                    "weapon": weapon,
                    "wear": wear,
                    "category": category,
                    "rarity": (x.get("rarity") or {}).get("name", ""),
                    "source": "dataset",
                })

        print(f"   构造完成: {len(items)} 个 market_hash_name")
        return items

    # ------------------------------------------------------------
    # 来源 B：SteamDT base
    # ------------------------------------------------------------

    def build_from_steamdt(self) -> List[Dict]:
        """从 SteamDT base 接口获取（⚠️ 消耗每日 1 次额度）"""
        print("📡 从 SteamDT base 接口获取（消耗每日额度）")
        with SteamDTClient() as client:
            raw = client.get_base_items()

        items = []
        for x in raw:
            if not isinstance(x, dict):
                continue
            mhn = x.get("marketHashName", "")
            if not mhn:
                continue
            items.append({
                "market_hash_name": mhn,
                "name_cn": x.get("name", ""),
                "weapon": mhn.split("|")[0].strip().lstrip("★ ").strip(),
                "wear": mhn.rsplit("(", 1)[-1].rstrip(")") if "(" in mhn else "",
                "category": "",
                "rarity": "",
                "source": "steamdt",
            })
        print(f"   获取完成: {len(items)} 条")
        return items

    # ------------------------------------------------------------
    # 合并与持久化
    # ------------------------------------------------------------

    @staticmethod
    def merge(*lists: List[Dict]) -> List[Dict]:
        """
        合并多个来源（以 market_hash_name 去重）。

        **后面的来源优先**：字段有值时会覆盖前面来源的同名字段。
        因此调用方应把更权威的来源放在后面（如 SteamDT 官方数据）。
        只在新来源该字段为空时，才保留前面来源的值。
        """
        merged: Dict[str, Dict] = {}

        for lst in lists:
            for item in lst:
                if not isinstance(item, dict) or not item.get("market_hash_name"):
                    continue

                key = item["market_hash_name"]

                if key not in merged:
                    merged[key] = dict(item)
                    continue

                cur = merged[key]
                for field in ("name_cn", "weapon", "wear", "category", "rarity", "source"):
                    new_val = item.get(field)
                    if new_val:                      # 后来源有值 → 覆盖
                        cur[field] = new_val

        return sorted(merged.values(), key=lambda x: x["market_hash_name"])

    def save(self, items: List[Dict]):
        from datetime import datetime
        payload = {
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items),
            "items": items,
        }
        with open(self.catalog_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"💾 已保存 {len(items)} 条 → {self.catalog_file}")

    def load(self) -> Optional[List[Dict]]:
        if not os.path.exists(self.catalog_file):
            return None
        try:
            with open(self.catalog_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("items")
            return items if isinstance(items, list) and items else None
        except (json.JSONDecodeError, OSError):
            return None

    def get_items(self, allow_build: bool = True) -> List[Dict]:
        """获取目录（优先本地缓存）"""
        cached = self.load()
        if cached is not None:
            return cached
        if not allow_build:
            raise FileNotFoundError(f"目录不存在: {self.catalog_file}")
        return self.rebuild()

    def rebuild(self, include_steamdt: bool = False) -> List[Dict]:
        """
        重建目录。

        参数:
            include_steamdt: 是否同时调用 SteamDT base（消耗每日 1 次额度）
        """
        sources = [self.build_from_dataset()]

        if include_steamdt:
            try:
                sources.append(self.build_from_steamdt())
            except SteamDTError as e:
                print(f"   ⚠️ SteamDT base 获取失败（{e}），仅使用开源数据集")

        # SteamDT 放在 sources 末尾 → merge 时覆盖开源数据集（官方数据更权威）
        items = self.merge(*sources)

        self.save(items)
        return items

    def info(self) -> Dict:
        """目录状态"""
        cached = self.load()
        if cached is None:
            return {"exists": False, "path": self.catalog_file}

        from collections import Counter
        by_cat = Counter(x.get("category", "?") for x in cached)
        return {
            "exists": True,
            "path": self.catalog_file,
            "count": len(cached),
            "by_category": dict(by_cat.most_common()),
        }


if __name__ == "__main__":
    catalog = ItemCatalog()
    items = catalog.rebuild()
    import json as _json
    print()
    print(_json.dumps(catalog.info(), ensure_ascii=False, indent=2))
    print()
    print("样例:")
    for it in items[:3] + items[-3:]:
        print(f"  {it['market_hash_name']:<48} {it['name_cn']}")
