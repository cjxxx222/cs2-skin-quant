"""
一键启动入口
支持命令行参数：
    python main.py                  # 启动看板（默认，含定时采集）
    python main.py --collect        # 采集数据后启动看板
    python main.py --collect-only   # 仅采集数据，不启动看板
    python main.py --schedule       # 启用每小时自动定时采集+监控扫描
    python main.py --no-schedule    # 禁用定时采集（仅手动触发）
"""

import os
import sys
import argparse
import webbrowser
import threading
import time
from datetime import datetime

os.environ["PYTHONIOENCODING"] = "utf-8"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

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

# 定时任务控制
_scheduler_running = False
_scheduler_thread = None


def open_browser(port: int, delay: float = 1.5):
    """延迟打开浏览器"""
    time.sleep(delay)
    webbrowser.open(f"http://127.0.0.1:{port}")


def collect_data():
    """执行数据采集（SteamDT 真实市场数据）"""
    from data.steamdt_collector import SteamDTCollector

    print("\n" + "=" * 60)
    print("📡 SteamDT 真实市场数据采集")
    print("=" * 60)

    collector = SteamDTCollector()
    ok, fail = collector.collect()

    print(f"\n采集统计: {ok} 成功, {fail} 失败")
    return ok, fail


def run_monitor_scan():
    """执行异常监控扫描"""
    from config import settings

    data_path = settings.DATA_FILE
    if not os.path.exists(data_path):
        print("⚠️ 数据文件不存在，跳过监控扫描")
        return []

    from engine.monitor import scan_alerts
    alerts = scan_alerts(data_path, settings.ANOMALY_THRESHOLDS)

    critical = [a for a in alerts if a["level"] == "critical"]
    warning = [a for a in alerts if a["level"] == "warning"]

    if critical or warning:
        print(f"\n🚨 异动告警: {len(critical)} 严重, {len(warning)} 警告")
        for a in critical[:5]:
            print(f"  🔴 {a['title']}")
        for a in warning[:3]:
            print(f"  🟡 {a['title']}")

    return alerts


def start_scheduler(interval_minutes: int = 60):
    """启动后台定时采集+监控"""
    global _scheduler_running, _scheduler_thread

    if _scheduler_running:
        return

    _scheduler_running = True

    def _schedule_loop():
        print(f"\n⏰ 定时任务已启动（每 {interval_minutes} 分钟采集+扫描一次）")
        while _scheduler_running:
            time.sleep(interval_minutes * 60)
            if not _scheduler_running:
                break
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n⏰ [{timestamp}] 定时采集触发...")
                collect_data()
                run_monitor_scan()
            except Exception as e:
                print(f"定时任务异常: {e}")

    _scheduler_thread = threading.Thread(target=_schedule_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    """停止定时任务"""
    global _scheduler_running
    _scheduler_running = False


def run_dashboard(skip_sample_check: bool = False, enable_schedule: bool = True):
    """启动看板服务"""
    from config import settings
    from web.app import app

    print("""
╔══════════════════════════════════════════════╗
║                                              ║
║  🔫 CSGO 皮肤量化交易系统 v2.0                ║
║     Signal A — 吸筹埋伏                        ║
║     Signal D — 板块补涨                        ║
║     🚨 实时异动监控                            ║
║                                              ║
╚══════════════════════════════════════════════╝
    """)

    print(f"📂 数据路径: {settings.DATA_FILE}")
    print(f"🌐 看板地址: http://127.0.0.1:{settings.WEB_PORT}")
    print()

    if not skip_sample_check:
        if not os.path.exists(settings.DATA_FILE):
            print("⚠️" + "=" * 56)
            print("⚠️  数据文件不存在！")
            print(f"⚠️  请将CSV放到: {settings.DATA_FILE}")
            print("⚠️  CSV格式: skin_name,date,price,volume")
            print("⚠️" + "=" * 56)
            print()
            print("📝 系统将生成示例数据供测试使用...")
            _create_sample_data(settings.DATA_FILE)
            print(f"✅ 示例数据已生成: {settings.DATA_FILE}")
            print()

    # 启动定时调度器
    if enable_schedule:
        interval = settings.COLLECTOR_CONFIG.get("schedule_interval_minutes", 60)
        start_scheduler(interval)
        print(f"⏰ 定时采集已启用 — 每 {interval} 分钟自动执行")
        print(f"📌 也可在看板中手动点击「🔍 立即扫描」按钮触发")
        print()

    # 自动打开浏览器
    threading.Thread(target=open_browser, args=(settings.WEB_PORT,), daemon=True).start()

    app.run(
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        debug=settings.WEB_DEBUG,
    )


def main():
    parser = argparse.ArgumentParser(description="CSGO 皮肤量化交易系统 v2.0")
    parser.add_argument(
        "--collect", action="store_true",
        help="启动前先执行数据采集"
    )
    parser.add_argument(
        "--collect-only", action="store_true",
        help="仅执行数据采集，不启动看板"
    )
    parser.add_argument(
        "--schedule", action="store_true", default=True,
        help="启用每小时定时采集+监控（默认启用）"
    )
    parser.add_argument(
        "--no-schedule", action="store_true",
        help="禁用定时采集（仅手动触发）"
    )
    args = parser.parse_args()

    enable_schedule = args.schedule and not args.no_schedule

    if args.collect_only:
        collect_data()
        run_monitor_scan()
    elif args.collect:
        print("🔄 正在执行数据采集...")
        collect_data()
        run_monitor_scan()
        print("\n🚀 数据采集完成，启动看板...")
        run_dashboard(skip_sample_check=True, enable_schedule=enable_schedule)
    else:
        run_dashboard(enable_schedule=enable_schedule)


def _create_sample_data(filepath: str):
    """生成示例CSV数据（供测试用，含 listings 字段）"""
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta

    skins = [
        "AK-47 | 火蛇", "AK-47 | 红线", "AK-47 | 燃料喷射器",
        "AWP | 二西莫夫", "AWP | 龙狙", "AWP | 野火",
        "M4A4 | 咆哮", "M4A1-S | 骑士", "M4A1-S | 金蛇",
        "沙漠之鹰 | 印花集", "USP-S | 击杀确认", "Glock-18 | 渐变之色",
    ]

    np.random.seed(42)
    end_date = datetime.now()
    records = []

    base_prices = {
        "AK-47 | 火蛇": 2500,
        "AK-47 | 红线": 150,
        "AK-47 | 燃料喷射器": 380,
        "AWP | 二西莫夫": 800,
        "AWP | 龙狙": 15000,
        "AWP | 野火": 450,
        "M4A4 | 咆哮": 6500,
        "M4A1-S | 骑士": 3500,
        "M4A1-S | 金蛇": 280,
        "沙漠之鹰 | 印花集": 120,
        "USP-S | 击杀确认": 200,
        "Glock-18 | 渐变之色": 180,
    }

    accumulation_skins = {"AK-47 | 红线", "沙漠之鹰 | 印花集", "M4A1-S | 金蛇"}
    laggard_skins = {"AWP | 野火", "USP-S | 击杀确认"}

    for skin in skins:
        price = base_prices[skin]
        listings = int(np.random.randint(20, 200))
        for i in range(120):
            date = end_date - timedelta(days=120 - i)
            daily_vol = int(np.random.lognormal(mean=5, sigma=0.6))
            listings = max(5, listings + int(np.random.normal(0, 3)))

            if skin in accumulation_skins and i >= 100:
                # 模拟吸筹阶段：横盘 + 放量 + 挂单减少
                price = price * (1 + np.random.normal(0, 0.002))
                daily_vol = int(daily_vol * (1.5 + (i - 100) * 0.15))
                listings = max(5, listings - int(np.random.exponential(3)))
            elif skin in laggard_skins and i >= 90:
                price = price * (1 + np.random.normal(0.0003, 0.005))
            else:
                price = price * (1 + np.random.normal(0, 0.008))

            price = max(price, 1.0)
            listings = max(listings, 1)

            records.append({
                "skin_name": skin,
                "date": date.strftime("%Y-%m-%d"),
                "price": round(price, 2),
                "volume": daily_vol,
                "listings": listings,
            })

    df = pd.DataFrame(records)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    df.to_csv(filepath, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
