"""
信号检测模块
— 信号A: 吸筹埋伏（缩量横盘 + 异常放量）
— 信号D: 板块补涨（同类已涨 + 本款滞涨）
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class Signal:
    """统一信号结构"""
    skin_name: str
    signal_type: str          # "A" 或 "D" 或 "A+D"
    level: str                # "strong" / "watch" / "info"
    score: int                # 0-100 综合评分
    reasons: List[str]        # 触发原因列表
    metrics: Dict[str, Any]   # 关键指标快照
    suggestion: str           # 操作建议


def detect_signal_a(
    skin_name: str,
    close: pd.Series,
    volume: pd.Series,
    config: dict,
    listings: Optional[pd.Series] = None,
    listings_median: Optional[float] = None,
) -> Optional[Signal]:
    """
    信号A — 吸筹埋伏检测

    逻辑：
    1. 近N日价格振幅 ≤ 阈值 → 窄幅横盘
    2. 流动性确认（挂单量）→ 吸筹/扫货迹象
    3. 价格在均线上方 → 趋势不破

    条件2 说明（SteamDT 无历史成交量，改用真实挂单量）：
      a) 有挂单量历史  → 检测"挂单量骤降"（有人在扫货，最直接的吸筹证据）
      b) 只有当日快照  → 检测"挂单量偏紧"（低于市场中位数，供给稀缺）
      c) 无挂单量数据  → 该条件不计分，并在 reasons 中注明"未验证"
    """
    c = config
    if len(close) < c["consolidation_days"] + c["volume_baseline_days"]:
        return None

    latest_close = close.iloc[-1]

    # ---------- 条件1: 窄幅横盘 ----------
    window = close.iloc[-c["consolidation_days"]:]
    amplitude = (window.max() - window.min()) / window.mean()
    cond1_ok = bool(amplitude <= c["consolidation_amplitude"])

    # ---------- 条件2: 流动性确认（挂单量）----------
    cond2_ok = False
    liquidity_note = ""
    listings_now = 0
    listings_drop_pct = None

    if listings is not None and len(listings) > 0:
        series = pd.Series(listings).fillna(0).reset_index(drop=True)
        nonzero = series[series > 0]

        if len(nonzero) > 0:
            listings_now = int(nonzero.iloc[-1])

        lookback = c.get("listings_lookback_days", 7)

        if len(nonzero) >= lookback + 1:
            # (a) 有历史 → 检测挂单量骤降（扫货）
            recent = nonzero.iloc[-1]
            baseline = nonzero.iloc[-(lookback + 1):-1].mean()
            if baseline > 0:
                ratio = recent / baseline
                listings_drop_pct = round((1 - ratio) * 100, 1)
                if ratio <= c.get("listings_drop_ratio", 0.85):
                    cond2_ok = True
                    liquidity_note = f"挂单量较近{lookback}日均值骤降{listings_drop_pct:.1f}%，疑似扫货吸筹"
                else:
                    liquidity_note = f"挂单量较近{lookback}日均值变化{(ratio - 1) * 100:+.1f}%，无明显扫货"
        elif listings_now > 0:
            # (b) 只有快照 → 检测挂单量是否偏紧
            if listings_median and listings_median > 0:
                scarce_ratio = c.get("listings_scarce_ratio", 0.5)
                if listings_now <= listings_median * scarce_ratio:
                    cond2_ok = True
                    liquidity_note = (
                        f"在售仅{listings_now}件，不足市场中位数({int(listings_median)}件)的"
                        f"{scarce_ratio:.0%}，供给偏紧"
                    )
                else:
                    liquidity_note = f"在售{listings_now}件，供给相对充足"
            else:
                liquidity_note = f"在售{listings_now}件（缺少市场基准，未判定）"
        else:
            liquidity_note = "无挂单量数据"
    else:
        liquidity_note = "无挂单量数据"

    # ---------- 条件3: 价格在均线上方 ----------
    ma = close.rolling(window=c["ma_period"], min_periods=c["ma_period"]).mean()
    cond3_ok = bool(latest_close > ma.iloc[-1]) if not pd.isna(ma.iloc[-1]) else False

    # ---------- 评分 ----------
    reasons = []
    score = 0

    if cond1_ok:
        score += 35
        reasons.append(f"近{c['consolidation_days']}日振幅仅{amplitude:.2%}，处于窄幅横盘")
    if cond2_ok:
        score += 35
        reasons.append(liquidity_note)
    elif listings_now > 0:
        # 流动性数据存在但未达标 → 如实说明，不计分
        reasons.append(f"流动性未确认：{liquidity_note}")
    if cond3_ok:
        score += 30
        reasons.append(f"价格位于{c['ma_period']}日均线上方，趋势完好")

    if score < 35:
        return None

    level = "strong" if score >= 65 else "watch"

    metrics = {
        "amplitude": round(amplitude * 100, 2),
        "latest_price": round(latest_close, 2),
        "price_vs_ma": "上方" if cond3_ok else "下方",
        "listings": listings_now,
        "liquidity_verified": "是" if cond2_ok else "否",
    }
    if listings_drop_pct is not None:
        metrics["listings_drop_pct"] = listings_drop_pct

    return Signal(
        skin_name=skin_name,
        signal_type="A",
        level=level,
        score=score,
        reasons=reasons,
        metrics=metrics,
        suggestion=_make_suggestion_a(level, cond2_ok),
    )


def _make_suggestion_a(level: str, liquidity_ok: bool = False) -> str:
    if level == "strong" and liquidity_ok:
        return "🟢 强烈关注：横盘蓄势 + 供给偏紧双重共振，吸筹迹象明显，可小仓位试探性建仓"
    if level == "strong":
        return "🟢 强烈关注：横盘形态成立且趋势完好，但流动性未确认，建议等待挂单量变化或放量突破再介入"
    if liquidity_ok:
        return "🟡 保持观察：供给端有收紧迹象，加入自选跟踪，等待价格形态进一步确认"
    return "🟡 保持观察：部分条件满足，加入自选持续跟踪，出现突破时跟进"


# ============================================================
# 信号D — 板块补涨
# ============================================================

def detect_signal_d(
    skin_name: str,
    close: pd.Series,
    volume: pd.Series,
    peer_returns: Dict[str, float],  # 同板块其他皮肤的30日涨幅
    config: dict,
) -> Optional[Signal]:
    """
    信号D — 板块补涨检测

    逻辑：
    1. 同板块中已有 ≥ N 款皮肤30日涨幅超过阈值
    2. 本款30日涨幅 < 最大滞后阈值（明显落后）
    3. 本款成交量未萎缩（市场仍在关注）
    """
    c = config
    if len(close) < c["peer_lookback_days"] + 1:
        return None

    # 条件1: 同板块有足够多的皮肤已经涨了
    rallied_peers = {k: v for k, v in peer_returns.items() if v >= c["peer_rally_threshold"]}
    cond1 = len(rallied_peers) >= c["min_peer_count"]
    cond1_ok = cond1

    # 条件2: 本款涨幅明显落后
    own_return = (close.iloc[-1] - close.iloc[-(c["peer_lookback_days"] + 1)]) / close.iloc[-(c["peer_lookback_days"] + 1)]
    cond2 = own_return < c["laggard_max_rise"]
    cond2_ok = bool(cond2)

    # 条件3: 成交量未萎缩（近10日均量 ≥ 30日均量的60%）
    vol_10d = volume.iloc[-10:].mean()
    vol_30d = volume.iloc[-30:].mean() if len(volume) >= 30 else volume.mean()
    vol_healthy = vol_10d >= vol_30d * 0.6 if vol_30d > 0 else False
    cond3 = vol_healthy
    cond3_ok = cond3

    reasons = []
    score = 0

    if cond1_ok:
        score += 40
        peer_names = ", ".join(list(rallied_peers.keys())[:3])
        reasons.append(f"同板块 {peer_names} 等已涨{c['peer_rally_threshold']:.0%}+，板块轮动启动")
    if cond2_ok:
        score += 35
        reasons.append(f"本款近{c['peer_lookback_days']}日仅涨{own_return:.2%}，严重滞后于板块")
    if cond3_ok:
        score += 25
        reasons.append("成交量健康，市场关注度仍在，补涨空间充足")

    if score < 40:
        return None

    level = "strong" if score >= 65 else "watch"

    # 计算补涨目标位（取同板块平均涨幅作为参考）
    avg_peer_return = np.mean(list(peer_returns.values())) if peer_returns else 0
    target_price = close.iloc[-1] * (1 + avg_peer_return)

    return Signal(
        skin_name=skin_name,
        signal_type="D",
        level=level,
        score=score,
        reasons=reasons,
        metrics={
            "own_return": round(own_return * 100, 2),
            "avg_peer_return": round(avg_peer_return * 100, 2),
            "rallied_peers": ", ".join(list(rallied_peers.keys())[:5]),
            "latest_price": round(close.iloc[-1], 2),
            "target_price": round(target_price, 2),
            "vol_healthy": "是" if cond3_ok else "否",
        },
        suggestion=_make_suggestion_d(level, avg_peer_return, target_price),
    )


def _make_suggestion_d(level: str, avg_peer_return: float, target_price: float) -> str:
    if level == "strong":
        return (
            f"🔵 板块补涨机会：同板块均值已涨{avg_peer_return:.1%}，"
            f"参考目标价 ¥{target_price:.0f}，建议在当前位置分批建仓"
        )
    return "🔵 关注补涨：板块有轮动迹象，继续观察确认趋势后介入"


# ============================================================
# 信号聚合
# ============================================================

def merge_signals(signal_a: Optional[Signal], signal_d: Optional[Signal]) -> Optional[Signal]:
    """
    如果一个皮肤同时触发A和D，合并为A+D超级信号
    """
    if signal_a and signal_d:
        combined_score = min(100, signal_a.score + signal_d.score // 2)
        return Signal(
            skin_name=signal_a.skin_name,
            signal_type="A+D",
            level="strong",
            score=combined_score,
            reasons=signal_a.reasons + signal_d.reasons,
            metrics={**signal_a.metrics, **signal_d.metrics},
            suggestion=f"🔥 双重信号共振！\n{signal_a.suggestion}\n{signal_d.suggestion}",
        )
    return signal_a or signal_d
