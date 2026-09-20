"""
饰品异常监控引擎
基于采集到的数据，检测：

1. 价格异动 — 24小时内涨/跌超过阈值
2. 成交量异动 — 当前小时成交量 > 过去24小时均量的N倍
3. 挂单量异动 — 在售数量突然减少超过阈值（有人在扫货）

输出结构化的告警列表，供 Web 看板使用
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd
import numpy as np


class AlertLevel(Enum):
    CRITICAL = "critical"   # 🔴 严重（多维度触发）
    WARNING = "warning"     # 🟡 警告（单维度触发）
    INFO = "info"           # 🔵 提示（接近阈值）


class AlertType(Enum):
    PRICE_SURGE = "price_surge"          # 📈 价格暴涨
    PRICE_CRASH = "price_crash"          # 📉 价格暴跌
    VOLUME_SPIKE = "volume_spike"        # 📊 成交量暴增
    LISTINGS_DROP = "listings_drop"      # 🛒 在售锐减（扫货）
    LISTINGS_SURGE = "listings_surge"    # 📦 在售暴增（抛售）
    MULTI_DIMENSION = "multi_dimension"  # 🔥 多维度共振


@dataclass
class Alert:
    """单条异动告警"""
    id: str                          # 唯一标识
    skin_name: str                   # 皮肤名称
    alert_type: AlertType            # 告警类型
    level: AlertLevel                # 严重程度
    title: str                       # 告警标题
    description: str                 # 详细描述
    metrics: Dict[str, Any]          # 关键数据
    triggered_rules: List[str]       # 触发了哪些规则
    timestamp: str                   # 告警时间
    suggestion: str                  # 操作建议


class AnomalyMonitor:
    """
    异常监控引擎

    用法：
        monitor = AnomalyMonitor(thresholds={...})
        alerts = monitor.scan("data/prices.csv")
    """

    def __init__(self, thresholds: Dict[str, Any] = None):
        """
        thresholds:
            price_change_24h_pct: float  — 24h涨跌幅阈值（默认 20%）
            volume_spike_ratio: float    — 成交量暴增倍数（默认 1.5x）
            listings_drop_pct: float     — 在售减少阈值（默认 -10%）
            listings_surge_pct: float    — 在售增加阈值（默认 +30%）
            price_warn_threshold: float  — 接近阈值时发出提示（默认 15%）
        """
        self.thresholds = {
            "price_change_24h_pct": 20.0,
            "volume_spike_ratio": 1.5,
            "listings_drop_pct": -10.0,
            "listings_surge_pct": 30.0,
            "price_warn_threshold": 15.0,
            "volume_warn_ratio": 1.3,
            "listings_warn_pct": -7.0,
        }
        if thresholds:
            self.thresholds.update(thresholds)

    # ============================================================
    # 主扫描入口
    # ============================================================

    def scan(self, csv_path: str) -> List[Alert]:
        """
        扫描全市场所有皮肤，返回告警列表
        """
        if not os.path.exists(csv_path):
            print(f"⚠️ 数据文件不存在: {csv_path}")
            return []

        df = pd.read_csv(csv_path)
        df["date"] = pd.to_datetime(df["date"])

        alerts = []

        # 按皮肤分组
        grouped = df.groupby("skin_name")
        total = len(grouped)

        for idx, (skin_name, group) in enumerate(grouped):
            if len(group) < 2:
                continue

            group = group.sort_values("date")

            # 逐个维度检测
            triggered = []

            # 1. 价格异动
            price_alert = self._check_price(skin_name, group)
            if price_alert:
                alerts.append(price_alert)
                triggered.append("price")

            # 2. 成交量异动
            volume_alert = self._check_volume(skin_name, group)
            if volume_alert:
                alerts.append(volume_alert)
                triggered.append("volume")

            # 3. 挂单量异动
            listing_alert = self._check_listings(skin_name, group)
            if listing_alert:
                alerts.append(listing_alert)
                triggered.append("listings")

            # 4. 多维度共振 → 升级告警
            if len(triggered) >= 2:
                # 合并之前的告警，生成一个超级告警
                # 保留原来的告警，额外加一个多维度共振告警
                multi_alert = self._merge_alert(skin_name, group, triggered)
                alerts = [a for a in alerts if a.skin_name != skin_name or a.alert_type == AlertType.MULTI_DIMENSION]
                # 简化处理：保留原有个体告警 + 首行插入多维度告警
                top_metrics = self._extract_metrics(group)
                multi = Alert(
                    id=f"multi_{skin_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    skin_name=skin_name,
                    alert_type=AlertType.MULTI_DIMENSION,
                    level=AlertLevel.CRITICAL,
                    title=f"🔥 多维度异动 — {skin_name}",
                    description=f"价格、成交量、挂单量同时异动，极可能有大资金在操作！",
                    metrics=top_metrics,
                    triggered_rules=triggered,
                    timestamp=datetime.now().isoformat(),
                    suggestion="⚠️ 强烈建议重点关注：多个指标同时触发，大概率是庄家吸筹或出货的前兆信号",
                )
                alerts.append(multi)

        # 按严重度排序
        level_order = {AlertLevel.CRITICAL: 0, AlertLevel.WARNING: 1, AlertLevel.INFO: 2}
        alerts.sort(key=lambda a: (level_order.get(a.level, 9), -abs(a.metrics.get("change_24h_pct", 0))))

        return alerts

    # ============================================================
    # 维度检测
    # ============================================================

    def _check_price(self, skin_name: str, group: pd.DataFrame) -> Optional[Alert]:
        """检测24h价格异动"""
        if "price" not in group.columns:
            return None

        latest = group.iloc[-1]
        latest_date = latest["date"]

        # 找24小时前的价格
        target_date = latest_date - timedelta(hours=24)
        earlier = group[group["date"] <= target_date]

        if earlier.empty:
            # 回退：用前一天的最后一条
            prev_day = group[group["date"].dt.date < latest_date.date()]
            if prev_day.empty and len(group) >= 2:
                prev_day = group.iloc[[-2]]
            if prev_day.empty:
                return None
            earlier_price = prev_day.iloc[-1]["price"]
        else:
            earlier_price = earlier.iloc[-1]["price"]

        if earlier_price <= 0:
            return None

        latest_price = latest["price"]
        change_pct = (latest_price - earlier_price) / earlier_price * 100

        threshold = self.thresholds["price_change_24h_pct"]
        warn_th = self.thresholds["price_warn_threshold"]

        alert_type = None
        level = None
        title = ""
        suggestion = ""

        if change_pct >= threshold:
            alert_type = AlertType.PRICE_SURGE
            level = AlertLevel.CRITICAL
            title = f"📈 价格暴涨 — {skin_name}"
            suggestion = f"24小时内涨幅达 {change_pct:.1f}%，远超正常波动。如果是跟风拉升，注意追高风险；如果是吸筹信号，可以小仓位试探。"
        elif change_pct <= -threshold:
            alert_type = AlertType.PRICE_CRASH
            level = AlertLevel.CRITICAL
            title = f"📉 价格暴跌 — {skin_name}"
            suggestion = f"24小时内跌幅达 {abs(change_pct):.1f}%，可能是恐慌抛售或庄家砸盘。观察成交量判断是洗盘还是出货。"
        elif abs(change_pct) >= warn_th:
            alert_type = AlertType.PRICE_SURGE if change_pct > 0 else AlertType.PRICE_CRASH
            level = AlertLevel.INFO
            direction = "涨" if change_pct > 0 else "跌"
            title = f"🔵 价格异动（接近阈值） — {skin_name}"
            suggestion = f"24小时内{direction}幅 {abs(change_pct):.1f}%，接近告警阈值，建议持续关注。"
        else:
            return None

        return Alert(
            id=f"price_{skin_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            skin_name=skin_name,
            alert_type=alert_type,
            level=level,
            title=title,
            description=f"24小时价格从 ¥{earlier_price:.2f} → ¥{latest_price:.2f}（{change_pct:+.1f}%）",
            metrics={
                "earlier_price": round(earlier_price, 2),
                "latest_price": round(latest_price, 2),
                "change_24h_pct": round(change_pct, 2),
            },
            triggered_rules=[f"24h涨跌幅 {abs(change_pct):.1f}% ≥ 阈值 {warn_th}%"],
            timestamp=datetime.now().isoformat(),
            suggestion=suggestion,
        )

    def _check_volume(self, skin_name: str, group: pd.DataFrame) -> Optional[Alert]:
        """检测成交量异动"""
        if "volume" not in group.columns:
            return None

        threshold = self.thresholds["volume_spike_ratio"]
        warn_ratio = self.thresholds["volume_warn_ratio"]

        # 取最近的数据
        if len(group) < 5:
            return None

        latest_vol = group.iloc[-1]["volume"]
        # 过去24小时的均量（取最近N条，排除最新1条）
        historical = group.iloc[:-1]
        if len(historical) > 24:
            historical = historical.iloc[-24:]

        avg_vol = historical["volume"].mean()
        if avg_vol <= 0:
            return None

        ratio = latest_vol / avg_vol

        if ratio >= threshold:
            level = AlertLevel.CRITICAL
            title = f"📊 成交量暴增 — {skin_name}"
            suggestion = f"当前成交量是近期均量的 {ratio:.1f} 倍，可能是大资金进场。如果价格同步上涨则是拉盘信号，如果价格未动则是吸筹信号。"
        elif ratio >= warn_ratio:
            level = AlertLevel.INFO
            title = f"🔵 成交量异动 — {skin_name}"
            suggestion = f"当前成交量是近期均量的 {ratio:.1f} 倍，建议持续观察。"
        else:
            return None

        return Alert(
            id=f"volume_{skin_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            skin_name=skin_name,
            alert_type=AlertType.VOLUME_SPIKE,
            level=level,
            title=title,
            description=f"当前成交量 {latest_vol} vs 近期均量 {avg_vol:.0f}（{ratio:.1f}x）",
            metrics={
                "latest_volume": int(latest_vol),
                "avg_volume_24h": int(avg_vol),
                "volume_ratio": round(ratio, 2),
            },
            triggered_rules=[f"成交量比 {ratio:.1f}x ≥ 阈值 {threshold}x"],
            timestamp=datetime.now().isoformat(),
            suggestion=suggestion,
        )

    def _check_listings(self, skin_name: str, group: pd.DataFrame) -> Optional[Alert]:
        """检测挂单量异动"""
        if "listings" not in group.columns:
            return None

        if len(group) < 2:
            return None

        latest = group.iloc[-1]
        previous = group.iloc[-2]

        latest_listings = latest["listings"]
        prev_listings = previous["listings"]

        if prev_listings <= 0:
            return None

        change_pct = (latest_listings - prev_listings) / prev_listings * 100

        drop_th = self.thresholds["listings_drop_pct"]
        surge_th = self.thresholds["listings_surge_pct"]
        warn_th = self.thresholds["listings_warn_pct"]

        if change_pct <= drop_th:
            level = AlertLevel.CRITICAL
            title = f"🛒 在售锐减（扫货） — {skin_name}"
            suggestion = f"在售数量骤减 {abs(change_pct):.1f}%，有人在大量扫货！如果多次出现此信号，极有可能是庄家吸筹，建议重点跟踪。"
        elif change_pct >= surge_th:
            level = AlertLevel.WARNING
            title = f"📦 在售暴增（抛售） — {skin_name}"
            suggestion = f"在售数量暴增 {change_pct:.1f}%，大资金可能在出货。如果价格同时下跌，是派发信号，建议减仓或观望。"
        elif change_pct <= warn_th:
            level = AlertLevel.INFO
            title = f"🔵 在售减少 — {skin_name}"
            suggestion = f"在售数量减少 {abs(change_pct):.1f}%，接近告警阈值，继续观察。"
        else:
            return None

        alert_type = AlertType.LISTINGS_DROP if change_pct < 0 else AlertType.LISTINGS_SURGE

        return Alert(
            id=f"listings_{skin_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            skin_name=skin_name,
            alert_type=alert_type,
            level=level,
            title=title,
            description=f"在售: {prev_listings} → {latest_listings}（{change_pct:+.1f}%）",
            metrics={
                "prev_listings": int(prev_listings),
                "latest_listings": int(latest_listings),
                "listings_change_pct": round(change_pct, 2),
            },
            triggered_rules=[f"在售变化 {change_pct:+.1f}% ≥ 阈值 {abs(drop_th) if change_pct < 0 else surge_th}%"],
            timestamp=datetime.now().isoformat(),
            suggestion=suggestion,
        )

    # ============================================================
    # 辅助
    # ============================================================

    def _extract_metrics(self, group: pd.DataFrame) -> Dict[str, Any]:
        """提取最新指标"""
        latest = group.iloc[-1]
        metrics = {
            "latest_price": round(float(latest.get("price", 0)), 2),
            "latest_volume": int(latest.get("volume", 0)),
            "latest_listings": int(latest.get("listings", 0)),
        }
        # 计算变化
        if len(group) >= 2:
            prev = group.iloc[-2]
            if prev.get("price", 0) > 0:
                metrics["change_24h_pct"] = round(
                    (latest["price"] - prev["price"]) / prev["price"] * 100, 2
                )
        return metrics

    def _merge_alert(self, skin_name: str, group: pd.DataFrame, triggered: List[str]) -> Optional[Alert]:
        """生成多维度共振告警"""
        return None  # 在主扫描中处理


# ============================================================
# 便捷函数（供 Web 调用）
# ============================================================

def scan_alerts(csv_path: str, thresholds: Dict = None) -> List[Dict]:
    """
    便捷函数：扫描并返回字典格式告警列表（供 API 使用）
    """
    monitor = AnomalyMonitor(thresholds)

    # 加载配置
    from config import settings
    if thresholds is None:
        thresholds = settings.ANOMALY_THRESHOLDS

    monitor.thresholds.update(thresholds)
    alerts = monitor.scan(csv_path)

    return [_alert_to_dict(a) for a in alerts]


def _alert_to_dict(a: Alert) -> Dict:
    return {
        "id": a.id,
        "skin_name": a.skin_name,
        "alert_type": a.alert_type.value,
        "level": a.level.value,
        "title": a.title,
        "description": a.description,
        "metrics": a.metrics,
        "triggered_rules": a.triggered_rules,
        "timestamp": a.timestamp,
        "suggestion": a.suggestion,
    }


if __name__ == "__main__":
    # 直接测试
    monitor = AnomalyMonitor()
    alerts = monitor.scan("data/prices.csv")
    print(f"\n发现 {len(alerts)} 个异动告警:")
    for a in alerts[:10]:
        print(f"  [{a.level.value}] {a.title}")
