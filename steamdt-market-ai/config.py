"""
SteamDT 全市场 AI 问答系统 — 全局配置
"""

import os

# ============================================================
# 路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")

# 全量饰品基础信息缓存（SteamDT 该接口每天只能调 1 次，必须落盘）
BASE_CACHE_FILE = os.path.join(CACHE_DIR, "steam_items_base.json")

# ============================================================
# SteamDT 开放平台
# ============================================================
STEAMDT = {
    "base_url": "https://open.steamdt.com",
    "env_key": "STEAMDT_API_KEY",
    "timeout": 30,

    # 各接口限速（秒/次），依据官方权限列表
    "rate_limit": {
        "base":         None,   # 每天 1 次 —— 由 base_cache 单独管理
        "kline":        0.5,    # 120 次/分钟
        "price_single": 1.0,    # 60 次/分钟
        "price_batch":  61.0,   # 1 次/分钟（谨慎）
        "price_avg":    1.0,    # 与 single 同档
        "wear":         0.1,    # 36000 次/小时
    },
}

# ============================================================
# 采集参数
# ============================================================
COLLECT = {
    # 一次批量价格查询最多多少个饰品（接口上限 100）
    "batch_size": 100,

    # price/batch 限速为每分钟 1 次，留出安全余量
    "batch_interval": 61.0,

    # K 线回溯天数上限
    "kline_days": 365,

    # 默认拉取 K 线的品种数量（按挂单量降序筛选）
    "kline_top_n": 500,

    # 优先平台（写入 listings 时作为主参考）
    "preferred_platforms": ["BUFF", "YOUPIN", "C5", "STEAM"],

    # 单次运行的最大批次数（None = 不限，用于调试时限制耗时）
    "max_batches": None,

    # 采集状态文件（支持中断续跑）
    "state_file": os.path.join(DATA_DIR, "collect_state.json"),
}

# ============================================================
# MySQL 数据库
# ============================================================
# 免安装版位置：D:\mysql-8.4.9-winx64
# ⚠️ 注意：MySQL 在 Windows 上不支持中文路径，安装目录必须是纯英文
DB = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",          # --initialize-insecure 初始化为空密码
    "database": "steamdt_market",
    "charset": "utf8mb4",
}

# MySQL 安装目录（用于启动/停止脚本）
MYSQL_HOME = r"D:\mysql-8.4.9-winx64"


def get_api_key() -> str:
    """从环境变量读取 SteamDT API Key"""
    key = os.environ.get(STEAMDT["env_key"], "").strip()
    if not key:
        raise RuntimeError(
            f"未配置 {STEAMDT['env_key']} 环境变量。\n"
            f"  Windows: setx {STEAMDT['env_key']} \"你的key\"  （设置后需重开终端）"
        )
    return key


def ensure_dirs():
    """确保数据目录存在"""
    os.makedirs(CACHE_DIR, exist_ok=True)
