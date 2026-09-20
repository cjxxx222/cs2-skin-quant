"""
CSGO皮肤量化交易系统 — 全局配置
"""

import os

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "prices.csv")  # 用户放置历史数据

# ============================================================
# 热门皮肤列表（Top 20 热门武器皮肤）
# ============================================================
# 格式: "武器名 | 皮肤名" — 用于在CSV中模糊匹配
HOT_SKINS = [
    # AK-47 系列
    "AK-47 | Redline (Field-Tested)",
    "AK-47 | Case Hardened (Field-Tested)",
    "AK-47 | Vulcan (Field-Tested)",
    "AK-47 | The Empress (Field-Tested)",
    # AWP 系列
    "AWP | Asiimov (Field-Tested)",
    "AWP | Dragon Lore (Field-Tested)",
    "AWP | Wildfire (Field-Tested)",
    "AWP | Neo-Noir (Field-Tested)",
    # M4 系列
    "M4A4 | Howl (Field-Tested)",
    "M4A4 | The Emperor (Field-Tested)",
    "M4A1-S | Printstream (Field-Tested)",
    "M4A1-S | Golden Coil (Field-Tested)",
    # 手枪
    "Desert Eagle | Printstream (Field-Tested)",
    "USP-S | Kill Confirmed (Field-Tested)",
    "Glock-18 | Fade (Factory New)",
]

# ============================================================
# 板块/系列分组（用于信号D — 板块补涨检测）
# ============================================================
COLLECTIONS = {
    "AK-47 系列": [
        "AK-47 | Redline (Field-Tested)",
        "AK-47 | Case Hardened (Field-Tested)",
        "AK-47 | Vulcan (Field-Tested)",
        "AK-47 | The Empress (Field-Tested)",
    ],
    "AWP 系列": [
        "AWP | Asiimov (Field-Tested)",
        "AWP | Dragon Lore (Field-Tested)",
        "AWP | Wildfire (Field-Tested)",
        "AWP | Neo-Noir (Field-Tested)",
    ],
    "M4 系列": [
        "M4A4 | Howl (Field-Tested)",
        "M4A4 | The Emperor (Field-Tested)",
        "M4A1-S | Printstream (Field-Tested)",
        "M4A1-S | Golden Coil (Field-Tested)",
    ],
    "手枪系列": [
        "Desert Eagle | Printstream (Field-Tested)",
        "USP-S | Kill Confirmed (Field-Tested)",
        "Glock-18 | Fade (Factory New)",
    ],
}

# ============================================================
# 信号A — 吸筹埋伏参数
# ============================================================
SIGNAL_A = {
    "consolidation_days": 14,       # 横盘观察窗口（天）
    "consolidation_amplitude": 0.03, # 横盘振幅阈值（3%）
    "volume_spike_days": 3,         # 近期成交量观察窗口
    "volume_baseline_days": 10,     # 成交量基线窗口
    "volume_spike_ratio": 2.0,      # 放量倍数阈值
    "ma_period": 30,                # 均线周期（价格需在均线上方）

    # --- 挂单量（SteamDT sellCount）替代成交量做吸筹判断 ---
    "listings_lookback_days": 7,    # 挂单量基线回溯天数
    "listings_drop_ratio": 0.85,    # 当前挂单量 ≤ 基线×0.85 → 骤降15%，疑似扫货
    "listings_scarce_ratio": 0.5,   # 挂单量 ≤ 市场中位数×0.5 → 供给偏紧
}

# ============================================================
# 信号D — 板块补涨参数
# ============================================================
SIGNAL_D = {
    "peer_rally_threshold": 0.15,   # 同板块其他皮肤涨幅阈值（15%）
    "min_peer_count": 2,            # 至少N款同板块皮肤已涨
    "laggard_max_rise": 0.05,       # 本款最大涨幅（<5%才算落后）
    "peer_lookback_days": 30,       # 回看天数
}

# ============================================================
# 技术指标参数
# ============================================================
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MA_PERIODS = [5, 10, 30]

# ============================================================
# Web 服务配置
# ============================================================
WEB_HOST = "127.0.0.1"
WEB_PORT = 5000
WEB_DEBUG = True

# ============================================================
# 异常监控阈值
# ============================================================
ANOMALY_THRESHOLDS = {
    # 价格异动：24小时涨跌幅超过此值触发告警
    "price_change_24h_pct": 20.0,   # 涨/跌 20% → 严重告警
    "price_warn_threshold": 15.0,   # 涨/跌 15% → 提示告警

    # 成交量异动：当前成交量 vs 近期均量
    "volume_spike_ratio": 1.5,      # > 1.5x → 成交暴增告警
    "volume_warn_ratio": 1.3,       # > 1.3x → 成交异动提示

    # 挂单量异动：在售数量变化
    "listings_drop_pct": -10.0,      # 减少 10% → 扫货告警
    "listings_surge_pct": 30.0,     # 增加 30% → 抛售告警
    "listings_warn_pct": -7.0,      # 减少 7% → 扫货提示
}

# ============================================================
# 数据采集配置
# ============================================================
COLLECTOR_CONFIG = {
    "api_config_path": "config/api_endpoints.json",
    "schedule_interval_minutes": 1440,  # 定时采集间隔（分钟）= 24小时（SteamDT 日K，每天采一次积累挂单量历史）
    "auto_collect_on_start": False,    # 启动时是否自动采集一次
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "request_timeout": 10,
}

# ============================================================
# 数据采集配置（Steam Market API）
# ============================================================
STEAM_APP_ID = "730"
STEAM_API_BASE_URL = "https://steamcommunity.com/market/priceoverview/"

COLLECTOR = {
    "currency": 2,
    "request_interval": 3.0,
    "request_timeout": 10,
    "max_retries": 3,
    "retry_delay": 5.0,
    "batch_size": 10,
    "auto_collect_interval_hours": 24,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "proxy": None,
    "use_steam_api": True,
}

# ============================================================
# AI 智能解读配置
# ============================================================
# 支持 OpenAI 兼容接口，DeepSeek / 智谱 / 通义 只需改 provider 对应两行。
# 默认用 DeepSeek（最省心）。API Key 请优先用环境变量 AI_API_KEY，
# 不要提交到代码仓库（key 泄露会被盗刷额度）。
AI_CONFIG = {
    "enabled": True,

    # 三选一：deepseek / zhipu / qwen
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com/chat/completions",
    "model": "deepseek-chat",

    # 从环境变量读取 key 的变量名
    "env_key": "AI_API_KEY",
    # 本地测试兜底：可直接填 key（正式使用请删除，改用环境变量）
    "api_key": "",

    "timeout": 60,
    "temperature": 0.7,
    "max_tokens": 1500,
}

# 若想切换其他平台，把上面 provider/base_url/model 改成：
#   智谱 GLM:  provider="zhipu", base_url="https://open.bigmodel.cn/api/paas/v4/chat/completions", model="glm-4-flash"
#   通义千问:  provider="qwen",  base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", model="qwen-turbo"

# ============================================================
# SteamDT 开放平台配置（真实市场数据源）
# ============================================================
# 文档: https://doc.steamdt.com/   申请 key: https://open.steamdt.com
# 接口频率: kline 120次/分钟, price/single 60次/分钟, base 每日1次
STEAMDT_CONFIG = {
    "enabled": True,
    "base_url": "https://open.steamdt.com",

    # 从环境变量读取 key 的变量名
    "env_key": "STEAMDT_API_KEY",
    # 本地测试兜底：可直接填 key（正式使用请删除，改用环境变量）
    # ⚠️ 切勿提交到 Git 仓库
    "api_key": "",

    "timeout": 20,
    "request_interval": 1.0,   # 每次请求间隔（秒），保守限速

    # 采集时使用的平台优先级（取第一个有数据的）
    "preferred_platforms": ["BUFF", "YOUPIN", "STEAM"],
}

# ============================================================
# 真实数据采集目标（SteamDT 需要"英文名 + 磨损后缀"）
# ============================================================
# 格式: SteamDT 的 marketHashName，必须带磨损后缀，否则接口返回空数据
STEAMDT_SKINS = [
    "AK-47 | Redline (Field-Tested)",
    "AK-47 | Case Hardened (Field-Tested)",
    "AK-47 | Vulcan (Field-Tested)",
    "AWP | Asiimov (Field-Tested)",
    "AWP | Dragon Lore (Field-Tested)",
    "AWP | Wildfire (Field-Tested)",
    "M4A4 | Howl (Field-Tested)",
    "M4A1-S | Printstream (Field-Tested)",
    "M4A1-S | Golden Coil (Field-Tested)",
    "Desert Eagle | Printstream (Field-Tested)",
    "USP-S | Kill Confirmed (Field-Tested)",
    "Glock-18 | Fade (Factory New)",
    "AK-47 | The Empress (Field-Tested)",
    "AWP | Neo-Noir (Field-Tested)",
    "M4A4 | The Emperor (Field-Tested)",
]
