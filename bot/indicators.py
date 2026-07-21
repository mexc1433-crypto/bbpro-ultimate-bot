"""
indicators.py
=============
Pure-Python implementations of the technical indicators used by the bot:
    - Bollinger Bands (SMA-based)
    - Relative Strength Index (Wilder's smoothing)
    - Exponential Moving Average
    - Average True Range (Wilder's smoothing)

These are deliberately framework-agnostic NumPy functions so the bot
remains independent of any specific TA library.  All functions accept
NumPy arrays and return arrays of the same length, padded with NaN at
the warm-up region so the caller can slice "last value" safely.
"""

import numpy as np
from typing import Tuple, Dict, Any


# ---------------------------------------------------------------------------
#  HELPERS
# ---------------------------------------------------------------------------
def _sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average, NaN-padded at the start."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average, NaN-padded at the start."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    # Seed with SMA of the first `period` values
    seed = np.mean(values[:period])
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _ema_with_nans(values: np.ndarray, period: int) -> np.ndarray:
    """
    Helper to calculate Exponential Moving Average, supporting leading NaN values.
    Pads with NaN until `period` non-NaN values are present.
    """
    out = np.full_like(values, np.nan, dtype=float)
    non_nan_indices = np.where(~np.isnan(values))[0]
    if len(non_nan_indices) < period:
        return out
    first_idx = non_nan_indices[0]
    if len(values) - first_idx < period:
        return out
    
    alpha = 2.0 / (period + 1.0)
    # Seed with SMA of the first `period` non-NaN values starting from first_idx
    seed = np.mean(values[first_idx : first_idx + period])
    out[first_idx + period - 1] = seed
    for i in range(first_idx + period, len(values)):
        if np.isnan(values[i]):
            out[i] = np.nan
        else:
            # If the previous value is NaN, try to look back further to find last non-nan EMA value
            prev_val = out[i - 1]
            if np.isnan(prev_val):
                # Look back to find the most recent valid EMA value
                valid_prev = out[first_idx + period - 1 : i]
                valid_prev = valid_prev[~np.isnan(valid_prev)]
                if len(valid_prev) > 0:
                    prev_val = valid_prev[-1]
                else:
                    prev_val = seed
            out[i] = alpha * values[i] + (1 - alpha) * prev_val
    return out


def _wilder_smoothing(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (used by RSI and ATR)."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    seed = np.mean(values[:period])
    out[period - 1] = seed
    alpha = 1.0 / period
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


# ---------------------------------------------------------------------------
#  BOLLINGER BANDS
# ---------------------------------------------------------------------------
def bollinger_bands(close: np.ndarray, period: int = 20,
                    deviations: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns three arrays: (middle, upper, lower).
    Middle = SMA(close, period).
    Upper  = Middle + deviations * Std.
    Lower  = Middle - deviations * Std.
    """
    middle = _sma(close, period)
    # Rolling std (population)
    std = np.full_like(close, np.nan, dtype=float)
    if len(close) >= period:
        for i in range(period - 1, len(close)):
            std[i] = np.std(close[i - period + 1 : i + 1], ddof=0)
    upper = middle + deviations * std
    lower = middle - deviations * std
    return middle, upper, lower


# ---------------------------------------------------------------------------
#  RSI  (Wilder)
# ---------------------------------------------------------------------------
def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index using Wilder's smoothing."""
    out = np.full_like(close, np.nan, dtype=float)
    if len(close) < period + 1:
        return out

    deltas = np.diff(close)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # First average gain/loss (simple mean of first `period` deltas)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    # Wilder smoothing for the rest
    for i in range(period + 1, len(close)):
        g = gains[i - 1]
        l = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))

    return out


# ---------------------------------------------------------------------------
#  EMA  (exposed alias)
# ---------------------------------------------------------------------------
def ema(close: np.ndarray, period: int) -> np.ndarray:
    return _ema(close, period)


# ---------------------------------------------------------------------------
#  ATR  (Wilder)
# ---------------------------------------------------------------------------
def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Average True Range using Wilder's smoothing."""
    n = len(close)
    out = np.full(n, np.nan, dtype=float)
    if n < period + 1:
        return out

    # True Range: max( H-L, |H-prevC|, |L-prevC| )
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl  = high[i] - low[i]
        hc  = abs(high[i] - close[i - 1])
        lc  = abs(low[i]  - close[i - 1])
        tr[i] = max(hl, hc, lc)

    # Seed ATR with simple mean of first `period` TRs
    seed = np.mean(tr[1:period + 1])
    out[period] = seed
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


# ---------------------------------------------------------------------------
#  NEW INDICATOR CALCULATIONS
# ---------------------------------------------------------------------------

def calc_stochastic(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    k_period: int = 5, d_period: int = 3, slowing: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    """
    Stochastic Oscillator.
    Returns (k, d) arrays of same length as input.
    """
    n = len(closes)
    k = np.full(n, np.nan, dtype=float)
    d = np.full(n, np.nan, dtype=float)
    if n < k_period:
        return k, d

    # Calculate raw %K
    raw_k = np.full(n, np.nan, dtype=float)
    for i in range(k_period - 1, n):
        sub_highs = highs[i - k_period + 1 : i + 1]
        sub_lows = lows[i - k_period + 1 : i + 1]
        hh = np.max(sub_highs)
        ll = np.min(sub_lows)
        diff = hh - ll
        if diff == 0:
            raw_k[i] = 100.0
        else:
            raw_k[i] = 100.0 * (closes[i] - ll) / diff

    # %K (slowing SMA of raw_k)
    for i in range(k_period + slowing - 2, n):
        k[i] = np.mean(raw_k[i - slowing + 1 : i + 1])

    # %D (SMA of %K)
    for i in range(k_period + slowing + d_period - 3, n):
        d[i] = np.mean(k[i - d_period + 1 : i + 1])

    return k, d


def calc_macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    MACD (Moving Average Convergence Divergence).
    Returns (macd_line, signal_line, histogram) arrays.
    """
    fast_ema = _ema_with_nans(closes, fast)
    slow_ema = _ema_with_nans(closes, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema_with_nans(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_williams_r(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Williams %R.
    Returns williams_r array.
    """
    n = len(closes)
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return out

    for i in range(period - 1, n):
        sub_highs = highs[i - period + 1 : i + 1]
        sub_lows = lows[i - period + 1 : i + 1]
        hh = np.max(sub_highs)
        ll = np.min(sub_lows)
        diff = hh - ll
        if diff == 0:
            out[i] = -50.0
        else:
            out[i] = -100.0 * (hh - closes[i]) / diff
    return out


def calc_cci(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 20) -> np.ndarray:
    """
    Commodity Channel Index.
    Returns cci array.
    """
    n = len(closes)
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return out

    tp = (highs + lows + closes) / 3.0
    for i in range(period - 1, n):
        sub_tp = tp[i - period + 1 : i + 1]
        tp_sma = np.mean(sub_tp)
        mean_dev = np.mean(np.abs(sub_tp - tp_sma))
        if mean_dev == 0:
            out[i] = 0.0
        else:
            out[i] = (tp[i] - tp_sma) / (0.015 * mean_dev)
    return out


def calc_vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """
    Volume Weighted Average Price (Cumulative).
    Returns vwap array.
    """
    tp = (highs + lows + closes) / 3.0
    tp_v = tp * volumes
    cumsum_tp_v = np.cumsum(tp_v)
    cumsum_v = np.cumsum(volumes)
    out = np.where(cumsum_v != 0, cumsum_tp_v / cumsum_v, tp)
    return out


def calc_pivot_points(high: Any, low: Any, close: Any) -> Dict[str, Any]:
    """
    Classic Pivot Points (Floor model).
    Supports both single values (scalars) and numpy arrays.
    """
    pp = (high + low + close) / 3.0
    r1 = 2.0 * pp - low
    s1 = 2.0 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2.0 * (pp - low)
    s3 = low - 2.0 * (high - pp)
    return {
        "PP": pp,
        "R1": r1,
        "R2": r2,
        "R3": r3,
        "S1": s1,
        "S2": s2,
        "S3": s3
    }


def calc_fibonacci(high: Any, low: Any) -> Dict[Any, Any]:
    """
    Calculate Fibonacci Retracement Levels.
    Supports both scalar and numpy arrays.
    """
    diff = high - low
    return {
        0.0: low,
        0.236: low + 0.236 * diff,
        0.382: low + 0.382 * diff,
        0.5: low + 0.5 * diff,
        0.618: low + 0.618 * diff,
        0.786: low + 0.786 * diff,
        1.0: high,
        # String key counterparts
        "0": low,
        "0.236": low + 0.236 * diff,
        "0.382": low + 0.382 * diff,
        "0.5": low + 0.5 * diff,
        "0.618": low + 0.618 * diff,
        "0.786": low + 0.786 * diff,
        "1.0": high
    }


def calc_heikin_ashi(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Heikin Ashi candles.
    Returns (ha_open, ha_high, ha_low, ha_close) arrays.
    """
    n = len(closes)
    ha_open = np.full(n, np.nan, dtype=float)
    ha_high = np.full(n, np.nan, dtype=float)
    ha_low = np.full(n, np.nan, dtype=float)
    ha_close = np.full(n, np.nan, dtype=float)
    
    if n == 0:
        return ha_open, ha_high, ha_low, ha_close
        
    ha_close = (opens + highs + lows + closes) / 4.0
    
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
        
    ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))
    
    return ha_open, ha_high, ha_low, ha_close


# ---------------------------------------------------------------------------
#  CONVENIENCE: bundle all indicators in one call
# ---------------------------------------------------------------------------
def compute_all_indicators(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                           cfg) -> dict:
    """
    Compute all indicators needed by the strategy in one pass.
    `cfg` is a BotConfig instance.
    Returns a dict with the latest usable values.
    """
    bb_mid, bb_up, bb_low = bollinger_bands(close, cfg.bb_period, cfg.bb_deviations)
    rsi_arr   = rsi(close, cfg.rsi_period)
    ema_fast  = ema(close, cfg.fast_ema_period)
    ema_slow  = ema(close, cfg.slow_ema_period)
    atr_arr   = atr(high, low, close, cfg.atr_period)

    return {
        "bb_mid":      bb_mid,
        "bb_upper":    bb_up,
        "bb_lower":    bb_low,
        "rsi":         rsi_arr,
        "ema_fast":    ema_fast,
        "ema_slow":    ema_slow,
        "atr":         atr_arr,
        "adx":         calc_adx(high, low, close, getattr(cfg, "adx_period", 14)),
    }


# ---------------------------------------------------------------------------
#  APPENDED UPGRADES
# ---------------------------------------------------------------------------

def calc_stochastic(highs, lows, closes, k_period=5, d_period=3, slowing=3):
    """Stochastic Oscillator"""
    import numpy as np
    n = len(closes)
    if n < k_period + d_period + slowing:
        return None, None
    raw_k = []
    for i in range(k_period - 1, n):
        hh = np.max(highs[i - k_period + 1:i + 1])
        ll = np.min(lows[i - k_period + 1:i + 1])
        if hh == ll:
            raw_k.append(50.0)
        else:
            raw_k.append(100.0 * (closes[i] - ll) / (hh - ll))
    raw_k = np.array(raw_k)
    # Slowing (simple MA of raw_k)
    slow_k = []
    for i in range(slowing - 1, len(raw_k)):
        slow_k.append(np.mean(raw_k[i - slowing + 1:i + 1]))
    slow_k = np.array(slow_k)
    # %D = MA of slow_k
    if len(slow_k) < d_period:
        return None, None
    d = []
    for i in range(d_period - 1, len(slow_k)):
        d.append(np.mean(slow_k[i - d_period + 1:i + 1]))
    return float(slow_k[-1]), float(d[-1])


def calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD Line, Signal Line, Histogram"""
    import numpy as np
    def ema(data, period):
        k = 2.0 / (period + 1)
        result = [data[0]]
        for v in data[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return np.array(result)
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return float(macd_line[-1]), float(signal_line[-1]), float(histogram[-1])


def calc_williams_r(highs, lows, closes, period=14):
    """Williams %R"""
    import numpy as np
    if len(closes) < period:
        return None
    hh = np.max(highs[-period:])
    ll = np.min(lows[-period:])
    if hh == ll:
        return -50.0
    return float(-100.0 * (hh - closes[-1]) / (hh - ll))


def calc_cci(highs, lows, closes, period=20):
    """Commodity Channel Index"""
    import numpy as np
    if len(closes) < period:
        return None
    tp = (np.array(highs[-period:]) + np.array(lows[-period:]) + np.array(closes[-period:])) / 3.0
    tp_mean = np.mean(tp)
    md = np.mean(np.abs(tp - tp_mean))
    if md == 0:
        return 0.0
    return float((tp[-1] - tp_mean) / (0.015 * md))


def calc_pivot_points(high, low, close):
    """Classic Pivot Points"""
    pp = (high + low + close) / 3.0
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return {'PP': pp, 'R1': r1, 'R2': r2, 'R3': r3, 'S1': s1, 'S2': s2, 'S3': s3}


def calc_fibonacci(high, low):
    """Fibonacci Retracement Levels"""
    diff = high - low
    return {
        '0.0': low,
        '0.236': low + 0.236 * diff,
        '0.382': low + 0.382 * diff,
        '0.500': low + 0.500 * diff,
        '0.618': low + 0.618 * diff,
        '0.786': low + 0.786 * diff,
        '1.0': high
    }


def calc_heikin_ashi(opens, highs, lows, closes):
    """Heikin Ashi candles"""
    import numpy as np
    n = len(closes)
    ha_close = (np.array(opens) + np.array(highs) + np.array(lows) + np.array(closes)) / 4.0
    ha_open = np.zeros(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
    ha_high = np.maximum(np.maximum(np.array(highs), ha_open), ha_close)
    ha_low = np.minimum(np.minimum(np.array(lows), ha_open), ha_close)
    return ha_open, ha_high, ha_low, ha_close


# ---------------------------------------------------------------------------
#  ADDED BY UPGRADE: H4 Trend, Market Structure, Volume Spike, BB Squeeze, ADX
# ---------------------------------------------------------------------------

def higher_timeframe_trend(bars_h4) -> str:
    """
    Determines the trend on H4 using EMA200.
    Returns "bullish" if close > EMA200, "bearish" if close < EMA200, or "flat".
    """
    if isinstance(bars_h4, np.ndarray):
        closes = bars_h4
    elif isinstance(bars_h4, list):
        if len(bars_h4) > 0 and hasattr(bars_h4[0], "close"):
            closes = np.array([b.close for b in bars_h4])
        elif len(bars_h4) > 0 and isinstance(bars_h4[0], dict):
            closes = np.array([b["close"] for b in bars_h4])
        else:
            closes = np.array(bars_h4)
    else:
        closes = np.array(bars_h4)

    if len(closes) < 200:
        return "flat"
    
    ema_200 = ema(closes, 200)
    if np.isnan(ema_200[-1]):
        return "flat"
    
    if closes[-1] > ema_200[-1]:
        return "bullish"
    elif closes[-1] < ema_200[-1]:
        return "bearish"
    return "flat"


def market_structure(bars) -> str:
    """
    Determines HH/HL (uptrend) or LH/LL (downtrend) or 'ranging'.
    """
    if isinstance(bars, np.ndarray):
        highs = bars
        lows = bars
    elif isinstance(bars, list):
        if len(bars) > 0 and hasattr(bars[0], "high"):
            highs = np.array([b.high for b in bars])
            lows = np.array([b.low for b in bars])
        elif len(bars) > 0 and isinstance(bars[0], dict):
            highs = np.array([b["high"] for b in bars])
            lows = np.array([b["low"] for b in bars])
        else:
            highs = np.array(bars)
            lows = np.array(bars)
    else:
        highs = np.array(bars)
        lows = np.array(bars)

    if len(highs) < 15:
        return "ranging"

    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] == np.max(highs[i-2 : i+3]):
            swing_highs.append(highs[i])
        if lows[i] == np.min(lows[i-2 : i+3]):
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    last_h1 = swing_highs[-1]
    last_h2 = swing_highs[-2]
    last_l1 = swing_lows[-1]
    last_l2 = swing_lows[-2]

    if last_h1 > last_h2 and last_l1 > last_l2:
        return "uptrend"
    elif last_h1 < last_h2 and last_l1 < last_l2:
        return "downtrend"
    return "ranging"


def volume_spike(bars, threshold: float = 1.5) -> bool:
    """
    Detects if the latest volume is above threshold * average volume.
    """
    if isinstance(bars, np.ndarray):
        volumes = bars
    elif isinstance(bars, list):
        if len(bars) > 0 and hasattr(bars[0], "volume"):
            volumes = np.array([b.volume for b in bars])
        elif len(bars) > 0 and isinstance(bars[0], dict):
            volumes = np.array([b["volume"] for b in bars])
        else:
            volumes = np.array(bars)
    else:
        volumes = np.array(bars)

    if len(volumes) < 21:
        return False

    latest_vol = volumes[-1]
    avg_vol = np.mean(volumes[-21:-1])
    if avg_vol == 0:
        return False
    return bool(latest_vol > threshold * avg_vol)


def bb_squeeze(bars, period: int = 20) -> bool:
    """
    Detects Bollinger Bands squeeze.
    """
    if isinstance(bars, np.ndarray):
        closes = bars
    elif isinstance(bars, list):
        if len(bars) > 0 and hasattr(bars[0], "close"):
            closes = np.array([b.close for b in bars])
        elif len(bars) > 0 and isinstance(bars[0], dict):
            closes = np.array([b["close"] for b in bars])
        else:
            closes = np.array(bars)
    else:
        closes = np.array(bars)

    if len(closes) < period * 2:
        return False

    middle, upper, lower = bollinger_bands(closes, period=period)
    bandwidth = (upper - lower) / middle
    valid_bw = bandwidth[~np.isnan(bandwidth)]
    if len(valid_bw) < period:
        return False
    
    current_bw = valid_bw[-1]
    avg_bw = np.mean(valid_bw[-period:])
    return bool(current_bw < avg_bw)


def calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Calculates Average Directional Index (ADX) using Wilder's smoothing.
    """
    n = len(close)
    adx_out = np.full(n, np.nan, dtype=float)
    if n < 2 * period:
        return adx_out

    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]

        if up > down and up > 0:
            plus_dm[i] = up
        else:
            plus_dm[i] = 0.0

        if down > up and down > 0:
            minus_dm[i] = down
        else:
            minus_dm[i] = 0.0

    smoothed_tr = _wilder_smoothing(tr, period)
    smoothed_plus_dm = _wilder_smoothing(plus_dm, period)
    smoothed_minus_dm = _wilder_smoothing(minus_dm, period)

    dx = np.full(n, np.nan, dtype=float)
    for i in range(period - 1, n):
        tr_val = smoothed_tr[i]
        p_dm = smoothed_plus_dm[i]
        m_dm = smoothed_minus_dm[i]

        if tr_val == 0 or np.isnan(tr_val) or np.isnan(p_dm) or np.isnan(m_dm):
            plus_di = 0.0
            minus_di = 0.0
        else:
            plus_di = 100.0 * (p_dm / tr_val)
            minus_di = 100.0 * (m_dm / tr_val)

        denom = plus_di + minus_di
        if denom == 0:
            dx_val = 0.0
        else:
            dx_val = 100.0 * abs(plus_di - minus_di) / denom
        dx[i] = dx_val

    non_nan_indices = np.where(~np.isnan(dx))[0]
    if len(non_nan_indices) < period:
        return adx_out
    
    first_idx = non_nan_indices[0]
    seed = np.mean(dx[first_idx : first_idx + period])
    adx_out[first_idx + period - 1] = seed
    for i in range(first_idx + period, n):
        adx_out[i] = (adx_out[i - 1] * (period - 1) + dx[i]) / period

    return adx_out
