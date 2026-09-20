"""
AI 智能解读服务
================
把系统扫描出的信号（信号A吸筹 / 信号D补涨）喂给大模型，
生成自然语言的市场解读。

设计原则：
1. 大模型只负责"翻译"和"归纳"，信号本身由系统规则引擎算出（可解释、可复现）
2. 未配置 API Key 时，退回规则生成的信号摘要（功能不中断）
3. 支持 OpenAI 兼容接口（DeepSeek / 智谱 / 通义 只需改配置）

用法示例：
    from web.ai_service import build_market_context, interpret_market
    ctx = build_market_context(skin_data_list)
    answer = interpret_market("哪些皮肤最值得关注？", ctx)
"""

import os
import sys

# 确保项目根目录在 path 中（支持直接运行本文件调试）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from config import settings
from engine.scanner import (
    get_signals_list,
    get_price_changes,
    get_anomaly_list,
    get_sector_heatmap,
    get_liquidity_ranking,
)


# ============================================================
# Prompt 设计（产品经理的核心活儿）
# ============================================================

SYSTEM_PROMPT = """你是一位资深的CSGO皮肤市场量化分析师。用户会给你系统扫描出的技术信号数据，请你用通俗易懂的中文解读当前市场情况。

请按以下结构回答：
1. 先给一句整体判断（当前市场偏多/偏空/震荡，机会多不多）
2. 挑出最值得关注的 2-3 个皮肤，解释为什么（结合信号类型 A吸筹/D补涨 和触发原因）
3. 指出风险点或需要警惕的地方
4. 语言口语化、专业但不装，整体控制在 300 字以内
5. 结尾固定加一句：以上为技术分析参考，不构成任何投资建议"""


# ============================================================
# 早报 Prompt（AI 市场每日早报专用）
# ============================================================

REPORT_SYSTEM_PROMPT = """你是一位专业的 CSGO 饰品市场分析师，每天为交易者撰写一份简洁、专业的「市场每日早报」。

你会收到系统整理好的市场数据（涨跌幅、成交量异动、板块表现、量化信号）。请严格按以下固定结构撰写早报，七个板块缺一不可：

## 早报结构

1. 📊 市场总览
   用 2-3 句话概括今日整体：市场偏强/偏弱/震荡、有几个交易信号、哪些板块活跃。

2. 🔥 涨幅榜 TOP
   列出涨幅靠前的 3-5 款皮肤，每款一句说明涨的逻辑（结合板块轮动或信号）。

3. 📉 跌幅榜 TOP
   列出跌幅靠前的 3-5 款皮肤，简述可能原因。

4. 📈 挂单量与流动性
   分析各品种的挂单量水平，说明市场含义：
   - 挂单量少 = 稀缺，价格容易被推高、波动大
   - 挂单量多 = 流动性好，容易成交、价格相对稳
   点出挂单量最少（最稀缺）和最多个别品种，结合价格给出判断。

5. 🗺️ 板块轮动
   分析哪个收藏品系列在走强、哪个在走弱。

6. ⚡ 信号解读
   解读系统检测到的吸筹(A)/补涨(D)信号，挑最重要的 2-3 个说明。

7. ⚠️ 风险提示
   1-2 句风险提醒，结尾固定加：以上内容为技术分析参考，不构成任何投资建议。

## 写作要求
- 语气专业、简洁，像券商给客户发的晨报，可适度口语化
- 全程中文，正文 300-500 字
- 只基于提供的真实数据，数据里没有的绝不编造
- 涨幅用 +x.x% 表示，跌幅用 -x.x% 表示，保留原始数值
- 用 emoji 标题 + 空行分段（纯文本格式，方便网页直接显示）"""


# ============================================================
# 核心：把信号转成大模型能读懂的上下文
# ============================================================

def build_market_context(skin_data_list, focus_skin=None, max_signals=15):
    """
    把扫描结果汇总成一段文本，作为给大模型的上下文（RAG 的"检索+组织"部分）。

    参数:
        skin_data_list: scan_all() 返回的 List[SkinData]
        focus_skin: 可选，聚焦某个皮肤（用于"解读当前皮肤"）
        max_signals: 最多塞进上下文的信号条数
    """
    signals = get_signals_list(skin_data_list)
    lines = []

    # 1. 全局概况
    total = len(skin_data_list)
    strong = sum(1 for s in signals if s["level"] == "strong")
    watch = sum(1 for s in signals if s["level"] == "watch")
    type_a = sum(1 for s in signals if "A" in s["signal_type"])
    type_d = sum(1 for s in signals if "D" in s["signal_type"])
    type_ad = sum(1 for s in signals if s["signal_type"] == "A+D")

    lines.append(
        f"【市场概况】共分析 {total} 款 CSGO 皮肤，当前有 {len(signals)} 个交易信号，"
        f"其中强烈信号 {strong} 个、关注信号 {watch} 个；"
        f"信号A(吸筹埋伏) {type_a} 个、信号D(板块补涨) {type_d} 个、A+D共振 {type_ad} 个。"
    )

    # 2. 信号明细（按评分从高到低，get_signals_list 已排好序）
    if signals:
        lines.append("\n【信号明细】（按评分从高到低）")
        for s in signals[:max_signals]:
            level_label = "🟢强烈" if s["level"] == "strong" else "🟡关注"
            reason_text = "；".join(s["reasons"])
            metric_text = "，".join(f"{k}={v}" for k, v in s["metrics"].items())
            lines.append(
                f"- {s['skin_name']} [{s['signal_type']}] {s['score']}分 {level_label}\n"
                f"  触发原因: {reason_text}\n"
                f"  关键指标: {metric_text}"
            )
    else:
        lines.append("\n【信号明细】当前没有检测到任何交易信号。")

    # 3. 聚焦某个皮肤
    if focus_skin:
        lines.append("\n" + _focus_skin_context(skin_data_list, focus_skin))

    return "\n".join(lines)


def _focus_skin_context(skin_data_list, focus_skin):
    """生成单个皮肤的聚焦上下文"""
    for sd in skin_data_list:
        if sd.name != focus_skin:
            continue

        df = sd.df.tail(30)
        if len(df) == 0:
            return f"【聚焦皮肤】{focus_skin}：无数据"

        latest = df.iloc[-1]
        ret_30 = (df["price"].iloc[-1] - df["price"].iloc[0]) / df["price"].iloc[0]

        parts = [
            f"【聚焦皮肤】{focus_skin}",
            f"  最新价 ¥{latest['price']:.2f}，近30日涨跌 {ret_30*100:+.1f}%，最新成交量 {int(latest['volume'])}",
        ]
        if sd.signal:
            s = sd.signal
            parts.append(f"  信号: {s.signal_type} {s.score}分 {s.level}")
            parts.append("  触发原因: " + "；".join(s.reasons))
            parts.append("  系统建议: " + s.suggestion)
        else:
            parts.append("  信号: 无（未触发吸筹埋伏或板块补涨条件）")
        return "\n".join(parts)

    return f"【聚焦皮肤】{focus_skin}：未找到该皮肤"


def build_report_context(skin_data_list, top_n=5):
    """
    构建「AI 市场早报」的数据上下文。

    把涨跌榜、成交量异动、板块轮动、量化信号汇总成结构化文本，
    作为喂给大模型的"早报素材"（RAG 的检索+组织部分）。
    """
    lines = []

    # 1. 涨跌榜
    changes = get_price_changes(skin_data_list, top_n=top_n)
    lines.append("【涨跌幅榜】（按30日）")
    lines.append("涨幅榜：")
    for r in changes["gainers"]:
        lines.append(
            f"  - {r['skin_name']}：现价¥{r['latest_price']}，"
            f"1日{r['change_1d']}%，7日{r['change_7d']}%，30日{r['change_30d']}%"
        )
    lines.append("跌幅榜：")
    for r in changes["losers"]:
        lines.append(
            f"  - {r['skin_name']}：现价¥{r['latest_price']}，"
            f"1日{r['change_1d']}%，7日{r['change_7d']}%，30日{r['change_30d']}%"
        )

    # 2. 挂单量与流动性（SteamDT 真实在售数量）
    liquidity = get_liquidity_ranking(skin_data_list, top_n=5)
    if liquidity["total"] > 0:
        lines.append("\n【挂单量与流动性】")
        lines.append("挂单量最少（最稀缺，价格易被推高）：")
        for r in liquidity["most_scarce"]:
            lines.append(
                f"  - {r['skin_name']}：仅 {r['listings']} 件在售，现价 ¥{r['latest_price']}"
            )
        lines.append("挂单量最多（流动性最好，易成交）：")
        for r in liquidity["most_liquid"]:
            lines.append(
                f"  - {r['skin_name']}：{r['listings']} 件在售，现价 ¥{r['latest_price']}"
            )
        lines.append("在售总市值最高（占用资金最多）：")
        for r in liquidity["market_value"]:
            lines.append(
                f"  - {r['skin_name']}：¥{r['market_value']:,.0f}"
                f"（{r['listings']} 件 × ¥{r['latest_price']}）"
            )
    else:
        lines.append("\n【挂单量与流动性】暂无挂单量数据（需运行 SteamDT 采集器）")

    # 3. 板块轮动
    sector = get_sector_heatmap(skin_data_list)
    if sector:
        lines.append("\n【板块表现】（30日平均涨幅）")
        for name, v in sorted(sector.items(), key=lambda kv: kv[1]["avg_return"], reverse=True):
            lines.append(f"  - {name}：{v['avg_return']}%")

    # 4. 量化信号
    signals = get_signals_list(skin_data_list)
    if signals:
        lines.append(f"\n【量化信号】共 {len(signals)} 个")
        for s in signals[:10]:
            level = "强烈" if s["level"] == "strong" else "关注"
            lines.append(f"  - {s['skin_name']} [{s['signal_type']}] {s['score']}分 {level}：{'；'.join(s['reasons'])}")
    else:
        lines.append("\n【量化信号】无")

    return "\n".join(lines)


# ============================================================
# 大模型调用
# ============================================================

def _get_api_key():
    """优先读环境变量，其次读配置兜底（正式环境请只用环境变量）"""
    cfg = settings.AI_CONFIG
    key = os.environ.get(cfg.get("env_key", "AI_API_KEY"), "").strip()
    if not key:
        key = cfg.get("api_key", "").strip()
    return key


def ask_ai(system_prompt, user_prompt):
    """调用大模型 API，返回文本回答（OpenAI 兼容格式）"""
    cfg = settings.AI_CONFIG

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "未配置 AI_API_KEY。请在启动前设置环境变量：\n"
            "  Windows (cmd):  set AI_API_KEY=sk-xxxx\n"
            "  或临时填在 config/settings.py 的 AI_CONFIG['api_key'] 里（仅限本地测试）"
        )

    payload = {
        "model": cfg.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 1500),
    }

    resp = requests.post(
        cfg["base_url"],
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=cfg.get("timeout", 60),
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def interpret_market(question, context):
    """组合 prompt，调用大模型解读市场"""
    user_prompt = f"以下是系统扫描结果：\n{context}"
    if question:
        user_prompt += f"\n\n用户额外提问：{question}"
    return ask_ai(SYSTEM_PROMPT, user_prompt)


def generate_daily_report(skin_data_list, top_n=5):
    """
    生成「AI 市场每日早报」。

    构建数据上下文 → 调大模型按 REPORT_SYSTEM_PROMPT 的固定结构撰写 → 返回早报全文。
    """
    context = build_report_context(skin_data_list, top_n=top_n)
    return ask_ai(REPORT_SYSTEM_PROMPT, context)


def summarize_signals(skin_data_list, focus_skin=None, top_n=5):
    """无 AI 时的规则兜底：生成简洁的中文信号摘要（不依赖大模型）"""
    signals = get_signals_list(skin_data_list)
    lines = []

    if focus_skin:
        lines.append(f"关于「{focus_skin}」：\n" + _focus_skin_context(skin_data_list, focus_skin) + "\n")

    lines.append(f"当前系统共扫描出 {len(signals)} 个交易信号，以下是评分最高的 {min(top_n, len(signals))} 个：\n")
    for s in signals[:top_n]:
        level = "🟢强烈" if s["level"] == "strong" else "🟡关注"
        lines.append(f"• {s['skin_name']} [{s['signal_type']}] {s['score']}分 {level}")
        lines.append(f"  {('；'.join(s['reasons']))}")
        lines.append("")

    if not signals:
        lines.append("当前没有检测到任何交易信号。")
    lines.append("（以上为规则生成，接入大模型后可获得更深入的归纳与风险提示）")
    return "\n".join(lines)


if __name__ == "__main__":
    # 快速自测：直接运行 python web/ai_service.py
    print(build_market_context.__doc__)
