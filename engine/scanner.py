"""
扫描引擎 — 编排所有检测逻辑
1. 加载数据
2. 逐个皮肤计算指标
3. 运行信号A + 信号D
4. 汇总结果
"""

import os
import sys
import pandas as pd
import numpy as np

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
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

from config import settings
from engine.indicators import compute_all
from engine.signals import (
    Signal,
    detect_signal_a,
    detect_signal_d,
    merge_signals,
)


@dataclass
class SkinData:
    """单个皮肤的完整数据"""
    name: str
    df: pd.DataFrame          # 原始OHLCV数据
    indicators: pd.DataFrame  # 技术指标
    signal: Optional[Signal]  # 检测到的信号


def load_data(data_path: str) -> pd.DataFrame:
    """
    加载CSV数据
    预期列: skin_name, date, price, volume
    可选列: open, high, low

    如果只有 price 列，用 price 填充 open/high/low
    """
    df = pd.read_csv(data_path)

    # 标准化列名
    col_map = {}
    for col in df.columns:
        col_lower = col.strip().lower()
        if col_lower in ("skin_name", "skin", "name"):
            col_map[col] = "skin_name"
        elif col_lower in ("date", "time", "datetime", "timestamp"):
            col_map[col] = "date"
        elif col_lower in ("price", "close", "收盘价"):
            col_map[col] = "price"
        elif col_lower in ("volume", "vol", "成交量"):
            col_map[col] = "volume"

    df = df.rename(columns=col_map)

    # 检查必要列
    required = ["skin_name", "date", "price", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV缺少必要列: {missing}。"
            f"当前列: {df.columns.tolist()}。"
            f"请确保包含 skin_name, date, price, volume 列。"
        )

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["skin_name", "date"]).reset_index(drop=True)

    return df


def filter_hot_skins(df: pd.DataFrame) -> pd.DataFrame:
    """只保留热门皮肤"""
    hot_list = settings.HOT_SKINS

    # 精确匹配 + 模糊匹配
    all_names = df["skin_name"].unique()
    matched = []
    for hot in hot_list:
        # 精确匹配
        if hot in all_names:
            matched.append(hot)
        else:
            # 模糊匹配（皮肤名包含关键词）
            fuzzy = [n for n in all_names if hot.lower() in n.lower()]
            matched.extend(fuzzy)

    matched = list(set(matched))

    if not matched:
        print(f"⚠️ 未匹配到热门皮肤！CSV中的皮肤: {all_names[:10]}...")
        return df  # 回退：返回全部数据

    print(f"✅ 匹配到 {len(matched)} 款热门皮肤")
    return df[df["skin_name"].isin(matched)]


def split_by_skin(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """按皮肤拆分DataFrame"""
    return {name: group.reset_index(drop=True) for name, group in df.groupby("skin_name")}


def compute_single_skin(
    name: str,
    skin_df: pd.DataFrame,
    listings_median: Optional[float] = None,
) -> Optional[SkinData]:
    """
    对单个皮肤执行完整分析流水线

    参数:
        listings_median: 全市场挂单量中位数，用于判断"供给是否偏紧"
    """
    if len(skin_df) < 40:
        print(f"  ⚠️ {name}: 数据不足（{len(skin_df)}条），跳过")
        return None

    close = skin_df["price"]
    volume = skin_df["volume"]
    listings = skin_df["listings"] if "listings" in skin_df.columns else None

    # 计算技术指标
    indicators = compute_all(close, volume, {
        "BOLLINGER_PERIOD": settings.BOLLINGER_PERIOD,
        "BOLLINGER_STD": settings.BOLLINGER_STD,
        "RSI_PERIOD": settings.RSI_PERIOD,
        "MACD_FAST": settings.MACD_FAST,
        "MACD_SLOW": settings.MACD_SLOW,
        "MACD_SIGNAL": settings.MACD_SIGNAL,
        "MA_PERIODS": settings.MA_PERIODS,
    })

    # 信号A: 吸筹埋伏（用挂单量做流动性确认）
    signal_a = detect_signal_a(
        name, close, volume, settings.SIGNAL_A,
        listings=listings, listings_median=listings_median,
    )

    # 信号D: 板块补涨
    signal_d = _check_signal_d_for_skin(name, close, volume, skin_df)

    # 合并信号
    signal = merge_signals(signal_a, signal_d)

    return SkinData(
        name=name,
        df=skin_df,
        indicators=indicators,
        signal=signal,
    )


def _check_signal_d_for_skin(
    name: str, close: pd.Series, volume: pd.Series, skin_df: pd.DataFrame
) -> Optional[Signal]:
    """
    为某个皮肤检测信号D（板块补涨）
    需要找到它所属的板块，以及同板块其他皮肤的涨跌情况
    """
    # 找到该皮肤所属的板块
    collection_name = None
    peers = []

    for col_name, skin_list in settings.COLLECTIONS.items():
        if name in skin_list:
            collection_name = col_name
            peers = [s for s in skin_list if s != name]
            break

    if not collection_name:
        return None  # 该皮肤不在任何板块中，跳过信号D

    # 计算同板块其他皮肤30日涨幅（需要跨皮肤数据）
    # 这里用 skin_df 自己算不了 — 需要外部传入同板块数据
    # 所以在 scan_all() 中会整体处理信号D
    return None  # 由 scan_all 统一处理


def scan_all(data_path: str) -> Tuple[List[SkinData], pd.DataFrame]:
    """
    全量扫描入口

    返回:
    - skin_data_list: 所有皮肤的分析结果（含信号）
    - full_df: 完整的原始数据
    """
    print("=" * 60)
    print("🔍 CSGO 皮肤量化扫描引擎")
    print("=" * 60)

    # 1. 加载数据
    print("\n📂 加载数据...")
    df = load_data(data_path)
    print(f"   共 {len(df)} 条记录，{df['skin_name'].nunique()} 款皮肤")
    print(f"   数据范围: {df['date'].min().date()} ~ {df['date'].max().date()}")

    # 2. 过滤热门皮肤
    print("\n🎯 筛选热门皮肤...")
    df = filter_hot_skins(df)
    skin_dict = split_by_skin(df)

    # 3. 逐个分析
    print(f"\n⚙️ 分析 {len(skin_dict)} 款皮肤...")
    skin_data_list: List[SkinData] = []

    # 先算全市场挂单量中位数（信号A 判断"供给偏紧"的基准）
    listings_values = []
    for _n, _df in skin_dict.items():
        if "listings" in _df.columns:
            _nz = _df["listings"].fillna(0)
            _nz = _nz[_nz > 0]
            if len(_nz) > 0:
                listings_values.append(float(_nz.iloc[-1]))
    listings_median = float(np.median(listings_values)) if listings_values else None
    if listings_median:
        print(f"   📊 市场挂单量中位数: {int(listings_median)} 件")

    for i, (name, skin_df) in enumerate(skin_dict.items()):
        print(f"  [{i+1}/{len(skin_dict)}] {name}...", end=" ")
        result = compute_single_skin(name, skin_df, listings_median=listings_median)
        if result:
            tag = ""
            if result.signal:
                tag = f" ⚡{result.signal.signal_type}({result.signal.score}分)"
            print(f"✅{tag}")
            skin_data_list.append(result)
        else:
            print(f"❌ 数据不足")

    # 4. 跨皮肤处理信号D（板块补涨）
    print("\n🔄 处理板块补涨信号（信号D）...")
    skin_data_list = _process_cross_skin_signal_d(skin_data_list, skin_dict)

    # 5. 汇总
    signals_found = [s for s in skin_data_list if s.signal]
    signals_found.sort(key=lambda s: s.signal.score, reverse=True)

    print("\n" + "=" * 60)
    print(f"✅ 扫描完成: {len(skin_data_list)} 款皮肤已分析")
    print(f"⚡ 发现信号: {len(signals_found)} 个")
    if signals_found:
        print("\n📊 信号排行:")
        for i, s in enumerate(signals_found):
            sig = s.signal
            print(f"  {i+1:2d}. [{sig.signal_type}] {s.name} — {sig.score}分 {sig.level}")
            for r in sig.reasons:
                print(f"      └ {r}")
    print("=" * 60)

    return skin_data_list, df


def _process_cross_skin_signal_d(
    skin_data_list: List[SkinData],
    skin_dict: Dict[str, pd.DataFrame],
) -> List[SkinData]:
    """
    跨皮肤处理信号D（板块补涨）
    需要同类皮肤之间的横向比较
    """
    # 构建 name -> SkinData 映射
    data_map = {sd.name: sd for sd in skin_data_list}

    for collection_name, skin_names in settings.COLLECTIONS.items():
        # 找到这个板块中在当前数据里的皮肤
        present = [n for n in skin_names if n in skin_dict]

        if len(present) < 2:
            continue

        # 计算每个皮肤30日涨幅
        returns = {}
        for name in present:
            close = skin_dict[name]["price"]
            if len(close) >= 31:
                ret = (close.iloc[-1] - close.iloc[-31]) / close.iloc[-31]
                returns[name] = ret
            else:
                returns[name] = 0

        # 对每个皮肤检测信号D
        for name in present:
            if name not in data_map:
                continue

            sd = data_map[name]
            peer_returns = {k: v for k, v in returns.items() if k != name}

            if not peer_returns:
                continue

            close = skin_dict[name]["price"]
            volume = skin_dict[name]["volume"]
            signal_d = detect_signal_d(name, close, volume, peer_returns, settings.SIGNAL_D)

            if signal_d:
                # 合并到现有信号
                sd.signal = merge_signals(sd.signal, signal_d)

    return skin_data_list


# ============================================================
# 便捷函数：为 Web 服务提供数据
# ============================================================

def get_signals_list(skin_data_list: List[SkinData]) -> List[dict]:
    """转换信号为前端友好的格式"""
    result = []
    for sd in skin_data_list:
        if not sd.signal:
            continue
        s = sd.signal
        result.append({
            "skin_name": s.skin_name,
            "signal_type": s.signal_type,
            "level": s.level,
            "score": s.score,
            "reasons": s.reasons,
            "metrics": s.metrics,
            "suggestion": s.suggestion,
        })
    result.sort(key=lambda x: x["score"], reverse=True)
    return result


def get_skin_chart_data(sd: SkinData) -> dict:
    """获取单个皮肤的图表数据"""
    df = sd.df
    ind = sd.indicators

    # 取最近90天数据
    df_tail = df.tail(90)
    ind_tail = ind.tail(90)

    return {
        "skin_name": sd.name,
        "dates": df_tail["date"].dt.strftime("%Y-%m-%d").tolist(),
        "price": df_tail["price"].tolist(),
        "volume": df_tail["volume"].tolist(),
        "bb_upper": ind_tail.get("bb_upper", pd.Series()).tolist(),
        "bb_middle": ind_tail.get("bb_middle", pd.Series()).tolist(),
        "bb_lower": ind_tail.get("bb_lower", pd.Series()).tolist(),
        "rsi": ind_tail.get("rsi", pd.Series()).tolist(),
        "macd": ind_tail.get("macd", pd.Series()).tolist(),
        "macd_signal": ind_tail.get("macd_signal", pd.Series()).tolist(),
        "macd_hist": ind_tail.get("macd_hist", pd.Series()).tolist(),
        "ma_5": ind_tail.get("ma_5", pd.Series()).tolist(),
        "ma_10": ind_tail.get("ma_10", pd.Series()).tolist(),
        "ma_30": ind_tail.get("ma_30", pd.Series()).tolist(),
        "volume_ma_10": ind_tail.get("volume_ma_10", pd.Series()).tolist(),
        "amplitude_14": ind_tail.get("amplitude_14", pd.Series()).tolist(),
        "ret_30d": ind_tail.get("ret_30d", pd.Series()).tolist(),
    }


def get_sector_heatmap(skin_data_list: List[SkinData]) -> list:
    """生成板块热力图数据"""
    collection_data = {}
    for col_name, skin_names in settings.COLLECTIONS.items():
        returns = []
        for sd in skin_data_list:
            if sd.name in skin_names:
                # 取30日涨幅
                ret_30d = sd.indicators.get("ret_30d", pd.Series())
                if len(ret_30d) > 0 and not pd.isna(ret_30d.iloc[-1]):
                    returns.append({
                        "name": sd.name,
                        "ret_30d": round(ret_30d.iloc[-1] * 100, 2),
                    })

        if returns:
            avg_ret = np.mean([r["ret_30d"] for r in returns])
            collection_data[col_name] = {
                "avg_return": round(avg_ret, 2),
                "skins": returns,
            }

    return collection_data


def get_anomaly_list(skin_data_list: List[SkinData]) -> list:
    """获取当日成交量异常的皮肤"""
    anomalies = []
    for sd in skin_data_list:
        ind = sd.indicators
        if len(ind) < 10:
            continue
        latest_vol = ind["volume"].iloc[-1]
        vol_ma = ind.get("volume_ma_10", pd.Series())
        if len(vol_ma) > 0 and not pd.isna(vol_ma.iloc[-1]) and vol_ma.iloc[-1] > 0:
            ratio = latest_vol / vol_ma.iloc[-1]
            if ratio >= 1.5 or ratio <= 0.5:
                anomalies.append({
                    "skin_name": sd.name,
                    "vol_ratio": round(ratio, 2),
                    "latest_volume": int(latest_vol),
                    "vol_ma": int(vol_ma.iloc[-1]),
                    "direction": "放量" if ratio >= 1.5 else "缩量",
                })

    anomalies.sort(key=lambda x: abs(x["vol_ratio"] - 1), reverse=True)
    return anomalies[:30]  # Top 30


def get_price_changes(skin_data_list: List[SkinData], top_n: int = 10) -> dict:
    """
    计算每款皮肤的涨跌幅（1日 / 7日 / 30日），返回涨跌榜。

    用途: 为「AI 市场早报」提供"涨幅榜 / 跌幅榜"数据源。

    返回:
        gainers: 按30日涨幅从高到低的前 top_n 款
        losers:  按30日涨幅从低到高的前 top_n 款
        每项含: skin_name, latest_price, change_1d, change_7d, change_30d
        数据不足对应周期时，该字段为 None
    """
    rows = []

    for sd in skin_data_list:
        prices = sd.df["price"]
        if len(prices) < 2:
            continue

        latest = float(prices.iloc[-1])

        def _pct(days: int):
            """近 days 天涨跌幅(%)，数据不足或基价为0时返回 None"""
            if len(prices) <= days or days <= 0:
                return None
            base = float(prices.iloc[-1 - days])
            if not base:
                return None
            return round((latest - base) / base * 100, 2)

        rows.append({
            "skin_name": sd.name,
            "latest_price": round(latest, 2),
            "change_1d": _pct(1),
            "change_7d": _pct(7),
            "change_30d": _pct(30),
        })

    # 只有具备30日数据的皮肤才参与涨跌榜排行（与 get_sector_heatmap 口径一致）
    ranked = [r for r in rows if r["change_30d"] is not None]
    gainers = sorted(ranked, key=lambda r: r["change_30d"], reverse=True)[:top_n]
    losers = sorted(ranked, key=lambda r: r["change_30d"])[:top_n]

    return {
        "gainers": gainers,
        "losers": losers,
        "top_n": top_n,
        "total": len(rows),
    }


def get_liquidity_ranking(skin_data_list: List[SkinData], top_n: int = 10) -> dict:
    """
    挂单量排行 —— 反映市场流动性与稀缺度。

    挂单量少 = 稀缺（价格易被推高，波动大）
    挂单量多 = 流动性好（易成交，价格稳）

    数据来源：SteamDT price/single 的 sellCount（真实在售数量）。
    注意：目前只有最新一天有挂单量数据，随每日采集逐步积累历史。

    返回:
        most_liquid: 挂单量最多的（流动性最好）
        most_scarce: 挂单量最少的（最稀缺）
        market_value: 按"挂单量×现价"排序（在售总市值，反映资金占用）
    """
    rows = []

    for sd in skin_data_list:
        df = sd.df
        if "listings" not in df.columns or len(df) == 0:
            continue

        # 取最后一个非零挂单量（当前只有最新一天有真实数据）
        listings_series = df["listings"].fillna(0)
        nonzero = listings_series[listings_series > 0]
        if len(nonzero) == 0:
            continue

        listings = int(nonzero.iloc[-1])
        latest_price = float(df["price"].iloc[-1])

        rows.append({
            "skin_name": sd.name,
            "listings": listings,
            "latest_price": round(latest_price, 2),
            # 在售总市值：挂单量 × 现价，反映该品种占用的市场资金
            "market_value": round(listings * latest_price, 2),
        })

    if not rows:
        return {"most_liquid": [], "most_scarce": [], "market_value": [], "total": 0}

    return {
        "most_liquid": sorted(rows, key=lambda r: r["listings"], reverse=True)[:top_n],
        "most_scarce": sorted(rows, key=lambda r: r["listings"])[:top_n],
        "market_value": sorted(rows, key=lambda r: r["market_value"], reverse=True)[:top_n],
        "total": len(rows),
    }
