"""
Flask Web 后端
提供 REST API + 看板页面
"""

import os
import sys
import threading

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template, request
from config import settings
from engine.scanner import (
    scan_all,
    get_signals_list,
    get_skin_chart_data,
    get_sector_heatmap,
    get_anomaly_list,
    SkinData,
)
from typing import List

app = Flask(__name__)

# 全局缓存扫描结果
_skin_data_cache: List[SkinData] = []
_raw_df_cache = None

# 采集状态
_collecting = False


def get_or_scan() -> List[SkinData]:
    """获取扫描结果（首次调用时执行扫描）"""
    global _skin_data_cache, _raw_df_cache
    if not _skin_data_cache:
        data_path = settings.DATA_FILE
        if not os.path.exists(data_path):
            raise FileNotFoundError(
                f"数据文件不存在: {data_path}\n"
                f"请将历史价格CSV放到 data/prices.csv\n"
                f"示例格式: skin_name,date,price,volume"
            )
        _skin_data_cache, _raw_df_cache = scan_all(data_path)
    return _skin_data_cache


# ============================================================
# 页面路由
# ============================================================

@app.route("/")
def index():
    """看板首页"""
    return render_template("dashboard.html")


# ============================================================
# API 路由
# ============================================================

@app.route("/api/signals")
def api_signals():
    """获取所有信号列表"""
    try:
        skin_data_list = get_or_scan()
        signals = get_signals_list(skin_data_list)
        return jsonify({"ok": True, "count": len(signals), "signals": signals})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/skin/<skin_name>")
def api_skin_detail(skin_name):
    """获取单个皮肤的完整图表数据"""
    try:
        skin_data_list = get_or_scan()
        for sd in skin_data_list:
            if sd.name == skin_name:
                chart = get_skin_chart_data(sd)
                # 附加信号信息
                if sd.signal:
                    chart["signal"] = {
                        "type": sd.signal.signal_type,
                        "level": sd.signal.level,
                        "score": sd.signal.score,
                        "reasons": sd.signal.reasons,
                        "suggestion": sd.signal.suggestion,
                        "metrics": sd.signal.metrics,
                    }
                else:
                    chart["signal"] = None
                return jsonify({"ok": True, "data": chart})
        return jsonify({"ok": False, "error": f"皮肤 '{skin_name}' 未找到"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/skins")
def api_skin_list():
    """获取所有皮肤名称列表"""
    try:
        skin_data_list = get_or_scan()
        names = []
        for sd in skin_data_list:
            has_signal = sd.signal is not None
            names.append({
                "name": sd.name,
                "has_signal": has_signal,
                "signal_type": sd.signal.signal_type if sd.signal else None,
                "score": sd.signal.score if sd.signal else 0,
            })
        # 有信号的排前面
        names.sort(key=lambda x: x["score"], reverse=True)
        return jsonify({"ok": True, "count": len(names), "skins": names})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/heatmap")
def api_heatmap():
    """板块热力图数据"""
    try:
        skin_data_list = get_or_scan()
        data = get_sector_heatmap(skin_data_list)
        return jsonify({"ok": True, "collections": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/anomalies")
def api_anomalies():
    """成交量异常列表"""
    try:
        skin_data_list = get_or_scan()
        anomalies = get_anomaly_list(skin_data_list)
        return jsonify({"ok": True, "count": len(anomalies), "anomalies": anomalies})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/overview")
def api_overview():
    """总览统计"""
    try:
        skin_data_list = get_or_scan()
        signals = [sd for sd in skin_data_list if sd.signal]

        strong = [sd for sd in signals if sd.signal.level == "strong"]
        watch = [sd for sd in signals if sd.signal.level == "watch"]

        type_a = [sd for sd in signals if "A" in sd.signal.signal_type]
        type_d = [sd for sd in signals if "D" in sd.signal.signal_type]
        type_ad = [sd for sd in signals if sd.signal.signal_type == "A+D"]

        # 各板块平均涨幅
        sector_data = get_sector_heatmap(skin_data_list)

        return jsonify({
            "ok": True,
            "total_skins": len(skin_data_list),
            "total_signals": len(signals),
            "strong_signals": len(strong),
            "watch_signals": len(watch),
            "signal_a_count": len(type_a),
            "signal_d_count": len(type_d),
            "signal_ad_count": len(type_ad),
            "sector_data": sector_data,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh")
def api_refresh():
    """强制刷新扫描"""
    global _skin_data_cache, _raw_df_cache
    _skin_data_cache = []
    _raw_df_cache = None
    try:
        get_or_scan()
        return jsonify({"ok": True, "message": "扫描已刷新"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/collect")
def api_collect():
    """触发数据采集"""
    global _collecting, _skin_data_cache, _raw_df_cache

    if _collecting:
        return jsonify({"ok": False, "error": "采集正在进行中，请稍后"})

    _collecting = True

    def do_collect():
        global _collecting, _skin_data_cache, _raw_df_cache
        try:
            from data.collector import SteamMarketCollector
            collector = SteamMarketCollector()
            collector.collect_all()
            _skin_data_cache = []
            _raw_df_cache = None
            get_or_scan()
        except Exception as e:
            print(f"采集失败: {e}")
        finally:
            _collecting = False

    threading.Thread(target=do_collect, daemon=True).start()
    return jsonify({"ok": True, "message": "数据采集已启动，请稍后刷新看板查看结果"})


@app.route("/api/collect/status")
def api_collect_status():
    """查询采集状态"""
    return jsonify({"ok": True, "collecting": _collecting})


# ============================================================
# 异常监控 API
# ============================================================
# 缓存监控结果
_monitor_alerts_cache = []


@app.route("/api/monitor/alerts")
def api_monitor_alerts():
    """获取异常监控告警列表"""
    global _monitor_alerts_cache
    try:
        data_path = settings.DATA_FILE
        if not os.path.exists(data_path):
            return jsonify({"ok": True, "count": 0, "alerts": [], "message": "数据文件尚未生成，请先采集数据"})

        from engine.monitor import scan_alerts
        _monitor_alerts_cache = scan_alerts(data_path, settings.ANOMALY_THRESHOLDS)

        critical = [a for a in _monitor_alerts_cache if a["level"] == "critical"]
        warning = [a for a in _monitor_alerts_cache if a["level"] == "warning"]
        info = [a for a in _monitor_alerts_cache if a["level"] == "info"]

        return jsonify({
            "ok": True,
            "count": len(_monitor_alerts_cache),
            "critical_count": len(critical),
            "warning_count": len(warning),
            "info_count": len(info),
            "alerts": _monitor_alerts_cache,
            "thresholds": settings.ANOMALY_THRESHOLDS,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/monitor/scan")
def api_monitor_scan():
    """强制刷新异常扫描"""
    global _monitor_alerts_cache
    try:
        data_path = settings.DATA_FILE
        if not os.path.exists(data_path):
            return jsonify({"ok": False, "error": "数据文件不存在，请先采集数据"})

        # 先采集最新数据
        try:
            from engine.collector import quick_collect
            collected = quick_collect()
        except Exception as e:
            print(f"采集跳过（可能API未配置）: {e}")

        # 执行异常扫描
        from engine.monitor import scan_alerts
        _monitor_alerts_cache = scan_alerts(data_path, settings.ANOMALY_THRESHOLDS)

        return jsonify({
            "ok": True,
            "message": "扫描完成",
            "count": len(_monitor_alerts_cache),
            "alerts": _monitor_alerts_cache,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/monitor/stats")
def api_monitor_stats():
    """获取监控统计摘要"""
    global _monitor_alerts_cache
    try:
        if not _monitor_alerts_cache:
            # 触发一次扫描
            data_path = settings.DATA_FILE
            if os.path.exists(data_path):
                from engine.monitor import scan_alerts
                _monitor_alerts_cache = scan_alerts(data_path, settings.ANOMALY_THRESHOLDS)

        critical = [a for a in _monitor_alerts_cache if a["level"] == "critical"]
        warning = [a for a in _monitor_alerts_cache if a["level"] == "warning"]

        # 按类型统计
        type_counts = {}
        for a in _monitor_alerts_cache:
            t = a["alert_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        return jsonify({
            "ok": True,
            "total": len(_monitor_alerts_cache),
            "critical": len(critical),
            "warning": len(warning),
            "by_type": type_counts,
            "top_alerts": _monitor_alerts_cache[:10],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================
# AI 智能解读 API
# ============================================================

@app.route("/api/ai/interpret", methods=["POST"])
def api_ai_interpret():
    """AI 智能解读：把扫描信号喂给大模型，生成自然语言市场解读"""
    try:
        from web.ai_service import build_market_context, interpret_market, summarize_signals

        data = request.get_json(silent=True) or {}
        skin_name = (data.get("skin_name") or "").strip()
        question = (data.get("question") or "").strip()

        skin_data_list = get_or_scan()
        context = build_market_context(skin_data_list, focus_skin=skin_name or None)

        try:
            answer = interpret_market(question, context)
            return jsonify({"ok": True, "answer": answer, "ai": True})
        except Exception as ai_err:
            # 未配置 key / 网络异常等：退回规则生成的信号摘要，功能不中断
            fallback = summarize_signals(skin_data_list, focus_skin=skin_name or None)
            return jsonify({
                "ok": True,
                "answer": fallback,
                "ai": False,
                "notice": (
                    f"AI 调用失败（{ai_err}），已回退为系统规则生成的信号摘要。"
                    "配置 AI_API_KEY 后即可获得自然语言解读。"
                ),
            })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ai/daily-report")
def api_ai_daily_report():
    """AI 市场每日早报：一键生成结构化的市场报告"""
    try:
        from web.ai_service import generate_daily_report, build_report_context

        skin_data_list = get_or_scan()

        try:
            report = generate_daily_report(skin_data_list)
            return jsonify({"ok": True, "report": report, "ai": True})
        except Exception as ai_err:
            # 未配置 key / 网络异常：退回规则整理的"早报素材"，功能不中断
            fallback = build_report_context(skin_data_list)
            return jsonify({
                "ok": True,
                "report": fallback,
                "ai": False,
                "notice": (
                    f"AI 调用失败（{ai_err}），已回退为系统整理的早报素材。"
                    "配置 AI_API_KEY 后即可获得完整早报。"
                ),
            })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("\n🚀 启动 CSGO 量化交易看板...")
    print(f"📍 访问地址: http://{settings.WEB_HOST}:{settings.WEB_PORT}")
    print(f"📂 数据文件: {settings.DATA_FILE}")
    print()

    app.run(
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        debug=settings.WEB_DEBUG,
    )
