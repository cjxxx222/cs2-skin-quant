"""
饰品名称智能匹配
================
全市场有数千款饰品，用户不可能打出完整的英文名（如
"AK-47 | Redline (Field-Tested)"）。本模块负责把用户的**口语化输入**
映射到准确的 marketHashName。

支持的输入形式：
    "ak 红线"           → AK-47 | Redline (Field-Tested)
    "AWP龙狙"           → AWP | Dragon Lore (Field-Tested)
    "usp"               → 所有 USP-S 系列
    "m4a1 印花集"       → M4A1-S | Printstream (...)
    "ak47 redline ft"   → AK-47 | Redline (Field-Tested)

匹配策略（按得分从高到低）：
    1. 完整名称精确命中
    2. 中文名精确命中
    3. 中文名子串命中
    4. marketHashName 子串命中
    5. 全部关键词命中（分词，忽略标点与大小写）
    6. 武器别名命中 + 其余关键词命中
"""

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 武器别名表
# ============================================================
# 左侧为「用户可能输入的写法」，右侧为「官方名称里出现的写法」
WEAPON_ALIASES: Dict[str, List[str]] = {
    # --- 步枪 ---
    "ak":       ["ak-47", "ak47"],
    "ak47":     ["ak-47", "ak47"],
    "m4":       ["m4a4", "m4a1-s", "m4a1s", "m4a1"],
    "m4a4":     ["m4a4"],
    "m4a1":     ["m4a1-s", "m4a1s"],
    "m4a1-s":   ["m4a1-s"],
    "加利尔":    ["galil"],
    "galil":    ["galil"],
    "法玛斯":    ["famas"],
    "famas":    ["famas"],
    "aug":      ["aug"],
    "sg553":    ["sg 553", "sg553"],
    "sg556":    ["sg 553", "sg553"],
    # --- 狙击枪 ---
    "awp":      ["awp"],
    "大狙":      ["awp"],
    "鸟狙":      ["ssg 08", "ssg08"],
    "ssg":      ["ssg 08", "ssg08"],
    "scar":     ["scar-20", "scar20"],
    "g3sg1":    ["g3sg1"],
    # --- 手枪 ---
    "glock":    ["glock-18", "glock18"],
    "格洛克":    ["glock-18", "glock18"],
    "usp":      ["usp-s", "usps"],
    "usp-s":    ["usp-s"],
    "usp消音版": ["usp-s", "usps"],
    "p2000":    ["p2000"],
    "p250":     ["p250"],
    "57":       ["five-seven", "fiveseven"],
    "tec9":     ["tec-9", "tec9"],
    "tec-9":    ["tec-9"],
    "cz":       ["cz75-auto", "cz75"],
    "cz75":     ["cz75-auto", "cz75"],
    "沙鹰":      ["desert eagle"],
    "沙漠之鹰":   ["desert eagle"],
    "deagle":   ["desert eagle"],
    "r8":       ["r8 revolver", "r8"],
    "左轮":      ["r8 revolver", "r8"],
    # --- 冲锋枪 ---
    "mac10":    ["mac-10", "mac10"],
    "mp9":      ["mp9"],
    "mp7":      ["mp7"],
    "mp5":      ["mp5-sd", "mp5"],
    "ump":      ["ump-45", "ump45"],
    "ump45":    ["ump-45", "ump45"],
    "p90":      ["p90"],
    "野牛":      ["pp-bizon", "bizon"],
    "bizon":    ["pp-bizon", "bizon"],
    # --- 霰弹枪 ---
    "nova":     ["nova"],
    "xm1014":   ["xm1014"],
    "mag7":     ["mag-7", "mag7"],
    "截短霰弹枪": ["sawed-off", "sawedoff"],
    # --- 机枪 ---
    "m249":     ["m249"],
    "内格夫":    ["negev"],
    "negev":    ["negev"],
    # --- 其他 ---
    "刀":        ["★"],
    "手套":      ["★"],
    "贴纸":      ["sticker"],
}

# 皮肤昵称 → 官方英文名（玩家平时不这么叫，但搜的时候会用）
# 官方中文名（如"巨龙传说"）在 base 列表的 name 字段里，可直接匹配；
# 这里补的是**口语昵称**与官方名的差异。
SKIN_ALIASES: Dict[str, List[str]] = {
    "龙狙":     ["dragon lore"],
    "咆哮":     ["howl"],
    "火蛇":     ["fire serpent"],
    "红线":     ["redline"],
    "二西莫夫":  ["asiimov"],
    "印花集":    ["printstream"],
    "渐变之色":  ["fade"],
    "渐变":     ["fade"],
    "火神":     ["vulcan"],
    "淬火":     ["case hardened"],
    "野火":     ["wildfire"],
    "金蛇":     ["golden coil"],
    "骑士":     ["knight"],
    "皇后":     ["the empress"],
    "大帝":     ["the emperor"],
    "龙王":     ["dragon king"],
    "血腥运动":  ["bloodsport"],
    "黑色魅影":  ["neo-noir"],
    "击杀确认":  ["kill confirmed"],
    "深红之网":  ["crimson web"],
    "多普勒":    ["doppler"],
    "大理石渐变": ["marble fade"],
    "传说":     ["lore"],
    "超导体":    ["superconductor"],
    "咆哮烈焰":  ["the empress"],
}

# 磨损档位的中英文对应
WEAR_ALIASES: Dict[str, str] = {
    "fn":  "factory new",
    "崭新出厂": "factory new",
    "崭新":  "factory new",
    "mw":  "minimal wear",
    "略有磨损": "minimal wear",
    "略磨":  "minimal wear",
    "ft":  "field-tested",
    "久经沙场": "field-tested",
    "久经":  "field-tested",
    "ww":  "well-worn",
    "破损不堪": "well-worn",
    "破损":  "well-worn",
    "bs":  "battle-scarred",
    "战痕累累": "battle-scarred",
    "战痕":  "battle-scarred",
}


def normalize(text: str) -> str:
    """标准化：转小写，去掉标点与空格，保留中文、字母、数字"""
    if not text:
        return ""
    return re.sub(r"[^\w一-鿿]", "", text.lower())


def tokenize(text: str) -> List[str]:
    """
    分词：把输入切成「连续中文」和「连续英数」两类片段。

        "ak 红线"      → ["ak", "红线"]
        "AWP龙狙ft"    → ["awp", "龙狙", "ft"]
        "m4a1 印花集"  → ["m4a1", "印花集"]
    """
    if not text:
        return []
    parts = re.findall(r"[一-鿿]+|[a-zA-Z0-9\-]+", text.lower())
    return [p.strip("-") for p in parts if p.strip("-")]


def _expand_aliases(token: str) -> List[str]:
    """把别名展开成官方写法（含磨损词）"""
    expanded = [token]

    # 武器别名
    if token in WEAPON_ALIASES:
        expanded.extend(WEAPON_ALIASES[token])

    # 皮肤昵称别名
    if token in SKIN_ALIASES:
        expanded.extend(SKIN_ALIASES[token])

    # 磨损别名（单独出现时也当作关键词）
    if token in WEAR_ALIASES:
        expanded.append(WEAR_ALIASES[token])

    return list(dict.fromkeys(expanded))  # 去重保序


def _token_hit(token: str, name_norm: str, market_norm: str) -> bool:
    """判断单个 token（含别名展开）是否命中目标名称"""
    for variant in _expand_aliases(token):
        v = normalize(variant)
        if v and (v in name_norm or v in market_norm):
            return True
    return False


def match_score(
    query: str,
    name: str,
    market_hash_name: str,
) -> Tuple[int, str]:
    """
    计算查询与单个饰品的匹配得分。

    返回: (得分, 命中的策略说明)；0 分表示不匹配。
    """
    if not query:
        return 0, ""

    q_raw = query.strip()
    q_norm = normalize(q_raw)
    name_norm = normalize(name or "")
    market_norm = normalize(market_hash_name or "")

    if not q_norm:
        return 0, ""

    # 1. 完整英文名精确命中
    if q_norm == market_norm:
        return 100, "英文名精确匹配"

    # 2. 完整中文名精确命中
    if q_norm == name_norm:
        return 95, "中文名精确匹配"

    # 3. 中文名子串
    if q_norm in name_norm:
        return 80, "中文名包含"

    # 4. 英文名子串
    if q_norm in market_norm:
        return 70, "英文名包含"

    # 5-6. 分词 / 别名匹配
    tokens = tokenize(q_raw)
    if not tokens:
        return 0, ""

    hits = [t for t in tokens if _token_hit(t, name_norm, market_norm)]

    if len(hits) == len(tokens):
        # 全部关键词都命中：词越多，匹配越可信
        return 50 + min(len(tokens), 5) * 2, f"全部关键词命中({len(tokens)}个)"

    # 部分命中：必须至少命中 1 个、且查询本身是多关键词，才给部分分
    # （否则单关键词 0 命中会被误判成"部分命中"）
    if len(tokens) > 1 and len(hits) >= 1 and len(hits) == len(tokens) - 1:
        return 30, f"部分关键词命中({len(hits)}/{len(tokens)})"

    return 0, ""


def extract_names(item: Dict) -> Tuple[str, str]:
    """
    从饰品字典中取出 (中文名, market_hash_name)。

    兼容两套字段命名：
        - SteamDT base 接口:  {"name": ..., "marketHashName": ...}
        - 本项目 item_catalog: {"name_cn": ..., "market_hash_name": ...}
    """
    if not isinstance(item, dict):
        return "", ""

    name_cn = (
        item.get("name_cn")
        or item.get("name")
        or ""
    )
    mhn = (
        item.get("market_hash_name")
        or item.get("marketHashName")
        or ""
    )
    return name_cn, mhn


def search(
    query: str,
    items: List[Dict],
    limit: int = 10,
    min_score: int = 30,
) -> List[Dict]:
    """
    在饰品列表中搜索。

    参数:
        query:     用户输入（中英文混合、口语化皆可）
        items:     饰品列表。字段命名兼容 SteamDT 格式与 item_catalog 格式
        limit:     返回条数上限
        min_score: 最低匹配分

    返回:
        按得分降序排列的结果，每项为原始 item 加上 _score / _reason 字段
    """
    results = []

    for item in items:
        name_cn, mhn = extract_names(item)
        if not mhn:
            continue

        score, reason = match_score(query, name_cn, mhn)
        if score >= min_score:
            hit = dict(item)
            hit["_score"] = score
            hit["_reason"] = reason
            results.append(hit)

    results.sort(key=lambda x: (-x["_score"], extract_names(x)[1]))
    return results[:limit]


if __name__ == "__main__":
    # 自测
    samples = [
        ("AK-47 | Redline (Field-Tested)", "AK-47 | 红线 (久经沙场)"),
        ("AWP | Dragon Lore (Field-Tested)", "AWP | 巨龙传说 (久经沙场)"),
        ("M4A1-S | Printstream (Minimal Wear)", "M4A1-S | 印花集 (略有磨损)"),
        ("USP-S | Kill Confirmed (Factory New)", "USP-S | 击杀确认 (崭新出厂)"),
    ]
    for q in ["ak 红线", "awp龙狙", "usp", "m4a1 印花集", "ak47 redline ft", "沙鹰"]:
        print(f"\n查询: {q!r}")
        for mk, nm in samples:
            s, r = match_score(q, nm, mk)
            if s:
                print(f"   [{s:>3}] {mk}   ({r})")
