"""
技术指标计算模块
— 布林带 / RSI / MACD / 均线 / 成交量均线
"""

import numpy as np
import pandas as pd
from scipy import stats


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均"""
    return series.rolling(window=period, min_periods=period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calc_bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    """
    布林带
    返回 DataFrame: [bb_upper, bb_middle, bb_lower, bb_width, bb_position]
    bb_position: 价格在布林带中的相对位置 (0=下轨, 1=上轨)
    """
    middle = calc_sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    width = (upper - lower) / middle  # 带宽百分比

    # 价格在带内的相对位置
    position = (close - lower) / (upper - lower)
    position = position.clip(0, 1)

    return pd.DataFrame({
        "bb_upper": upper,
        "bb_middle": middle,
        "bb_lower": lower,
        "bb_width": width,
        "bb_position": position,
    })


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI — 相对强弱指标
    使用 Wilder's smoothing
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    MACD
    返回 DataFrame: [macd, macd_signal, macd_hist]
    macd_hist > 0 表示 MACD 在信号线上方
    """
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": histogram,
    })


def calc_volume_ma(volume: pd.Series, period: int = 10) -> pd.Series:
    """成交量均线"""
    return volume.rolling(window=period, min_periods=period).mean()


def calc_amplitude(close: pd.Series, period: int) -> pd.Series:
    """
    计算滚动窗口内的价格振幅
    振幅 = (最高价 - 最低价) / 平均值
    注意：如果只有收盘价，用 (max(close) - min(close)) / mean(close)
    """
    rolling_max = close.rolling(window=period, min_periods=period).max()
    rolling_min = close.rolling(window=period, min_periods=period).min()
    rolling_mean = close.rolling(window=period, min_periods=period).mean()
    return (rolling_max - rolling_min) / rolling_mean


def calc_returns(close: pd.Series, period: int) -> pd.Series:
    """计算N日涨跌幅"""
    return close.pct_change(periods=period)


def calc_linear_slope(series: pd.Series, period: int) -> pd.Series:
    """
    计算滚动线性回归斜率（用于判断趋势方向）
    正值 = 上升趋势，负值 = 下降趋势
    """
    def _slope(y):
        if len(y) < period:
            return np.nan
        x = np.arange(len(y))
        slope, _, _, _, _ = stats.linregress(x, y)
        return slope

    return series.rolling(window=period, min_periods=period).apply(_slope, raw=False)


def compute_all(close: pd.Series, volume: pd.Series, settings: dict) -> pd.DataFrame:
    """
    一键计算所有技术指标，返回完整 DataFrame
    """
    df = pd.DataFrame(index=close.index)
    df["close"] = close
    df["volume"] = volume

    # 布林带
    bb = calc_bollinger(close, settings["BOLLINGER_PERIOD"], settings["BOLLINGER_STD"])
    df = pd.concat([df, bb], axis=1)

    # RSI
    df["rsi"] = calc_rsi(close, settings["RSI_PERIOD"])

    # MACD
    macd = calc_macd(close, settings["MACD_FAST"], settings["MACD_SLOW"], settings["MACD_SIGNAL"])
    df = pd.concat([df, macd], axis=1)

    # 多周期均线
    for period in settings["MA_PERIODS"]:
        df[f"ma_{period}"] = calc_sma(close, period)

    # 成交量均线
    df["volume_ma_10"] = calc_volume_ma(volume, 10)

    # 滚动振幅
    df["amplitude_14"] = calc_amplitude(close, 14)

    # 涨跌幅
    df["ret_1d"] = calc_returns(close, 1)
    df["ret_7d"] = calc_returns(close, 7)
    df["ret_30d"] = calc_returns(close, 30)

    # 趋势斜率（30日）
    df["trend_slope_30"] = calc_linear_slope(close, 30)

    return df
