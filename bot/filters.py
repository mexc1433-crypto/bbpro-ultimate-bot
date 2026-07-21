"""
filters.py - BBPro Ultimate v2
Multi-layer signal filters for higher win rate.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def spread_ok(spread_pips: float, max_spread: float = 3.0) -> bool:
    return spread_pips <= max_spread


def rsi_ok(rsi: float, side: str) -> bool:
    """RSI filter: avoid overbought/oversold entries."""
    if side.lower() == "buy":
        return rsi < 70          # don't buy when overbought
    else:
        return rsi > 30          # don't sell when oversold


def adx_ok(adx: float, min_adx: float = 20.0) -> bool:
    """ADX filter: only trade trending markets."""
    return adx >= min_adx


def ema_trend_ok(close: float, ema50: float, ema200: float, side: str) -> bool:
    """EMA trend filter: trade with the trend."""
    if side.lower() == "buy":
        return close > ema50 and ema50 > ema200
    else:
        return close < ema50 and ema50 < ema200


def higher_tf_trend(bars_h4: list, side: str) -> bool:
    """
    H4 trend filter using EMA200.
    bars_h4: list of dicts with 'close' key, newest last.
    """
    if not bars_h4 or len(bars_h4) < 200:
        return True  # not enough data, allow trade
    closes = [b["close"] if isinstance(b, dict) else b for b in bars_h4]
    ema200 = sum(closes[-200:]) / 200
    last_close = closes[-1]
    if side.lower() == "buy":
        return last_close > ema200
    else:
        return last_close < ema200


def market_structure(bars: list, side: str) -> bool:
    """
    Market structure: BUY = HH/HL (uptrend), SELL = LH/LL (downtrend).
    Uses last 4 swings from bars.
    """
    if not bars or len(bars) < 6:
        return True
    closes = [b["close"] if isinstance(b, dict) else b for b in bars[-6:]]
    highs  = [b["high"]  if isinstance(b, dict) else b for b in bars[-6:]]
    lows   = [b["low"]   if isinstance(b, dict) else b for b in bars[-6:]]
    if side.lower() == "buy":
        # uptrend: recent high > prev high AND recent low > prev low
        return highs[-1] > highs[-3] and lows[-1] > lows[-3]
    else:
        return highs[-1] < highs[-3] and lows[-1] < lows[-3]


def volume_spike(bars: list, threshold: float = 1.5) -> bool:
    """Detect volume spike above average (uses tick_volume if available)."""
    if not bars or len(bars) < 10:
        return True
    vols = [b.get("volume", 1) if isinstance(b, dict) else 1 for b in bars[-10:]]
    if all(v == 1 for v in vols):
        return True  # no volume data available
    avg = sum(vols[:-1]) / len(vols[:-1])
    return vols[-1] >= avg * threshold


def bb_squeeze(bars: list, period: int = 20) -> bool:
    """
    Bollinger Band Squeeze: detect when bands are narrow (low volatility → upcoming breakout).
    Returns True if current bandwidth is below 50% of its 20-period average.
    """
    if not bars or len(bars) < period * 2:
        return False
    closes = [b["close"] if isinstance(b, dict) else b for b in bars]

    def bw(subset):
        mean = sum(subset) / len(subset)
        std = (sum((x - mean) ** 2 for x in subset) / len(subset)) ** 0.5
        return (2 * std) / mean if mean else 0

    recent_bw = bw(closes[-period:])
    historical_bws = [bw(closes[i:i+period]) for i in range(len(closes)-period*2, len(closes)-period)]
    avg_bw = sum(historical_bws) / len(historical_bws) if historical_bws else recent_bw
    return recent_bw < avg_bw * 0.5


def news_blackout(side: str = "") -> bool:
    """
    Simple time-based news blackout.
    Avoid trading 30 min before/after major news hours (8:30 UTC, 13:30 UTC).
    Returns True if it's SAFE to trade.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    h, m = now.hour, now.minute
    # Major news windows: 08:25-09:00 UTC, 13:25-14:00 UTC
    blackout_windows = [(8, 25, 9, 0), (13, 25, 14, 0)]
    for (sh, sm, eh, em) in blackout_windows:
        start_mins = sh * 60 + sm
        end_mins   = eh * 60 + em
        now_mins   = h  * 60 + m
        if start_mins <= now_mins <= end_mins:
            return False  # blackout
    return True


def multi_layer_filter(side: str, indicators: dict) -> dict:
    """
    Master filter — checks all layers and returns score + decision.
    
    indicators dict keys:
        rsi, adx, close, ema50, ema200, spread_pips, bars (list), bars_h4 (list)
    
    Returns:
        { "pass": bool, "score": int (0-100), "reasons": [str] }
    """
    checks = {}
    reasons = []

    # 1. RSI filter
    rsi = indicators.get("rsi", 50)
    checks["rsi"] = rsi_ok(rsi, side)
    if not checks["rsi"]:
        reasons.append(f"RSI={rsi:.1f} في منطقة مشبعة")

    # 2. ADX filter
    adx = indicators.get("adx", 25)
    checks["adx"] = adx_ok(adx)
    if not checks["adx"]:
        reasons.append(f"ADX={adx:.1f} سوق رينج")

    # 3. EMA trend
    close  = indicators.get("close", 0)
    ema50  = indicators.get("ema50", 0)
    ema200 = indicators.get("ema200", 0)
    if close and ema50 and ema200:
        checks["ema_trend"] = ema_trend_ok(close, ema50, ema200, side)
        if not checks["ema_trend"]:
            reasons.append("EMA trend عكسي")
    else:
        checks["ema_trend"] = True  # skip if no data

    # 4. Spread
    spread = indicators.get("spread_pips", 1.0)
    checks["spread"] = spread_ok(spread)
    if not checks["spread"]:
        reasons.append(f"Spread={spread:.1f} عالي")

    # 5. News blackout
    checks["news"] = news_blackout(side)
    if not checks["news"]:
        reasons.append("نافذة أخبار")

    # 6. Market structure (if bars available)
    bars = indicators.get("bars", [])
    if len(bars) >= 6:
        checks["structure"] = market_structure(bars, side)
        if not checks["structure"]:
            reasons.append("هيكل السوق عكسي")
    else:
        checks["structure"] = True

    # 7. H4 trend (if h4 bars available)
    bars_h4 = indicators.get("bars_h4", [])
    if len(bars_h4) >= 200:
        checks["h4_trend"] = higher_tf_trend(bars_h4, side)
        if not checks["h4_trend"]:
            reasons.append("H4 trend عكسي")
    else:
        checks["h4_trend"] = True

    # Score: each check = ~14 points
    passed = sum(1 for v in checks.values() if v)
    total  = len(checks)
    score  = int((passed / total) * 100)

    # Need at least 4/7 checks to pass (score >= 57)
    decision = (score >= 57)

    if decision:
        logger.info("Signal PASSED: score=%d/100 (%d/%d checks) side=%s", score, passed, total, side)
    else:
        logger.info("Signal BLOCKED: score=%d/100, reasons=%s", score, reasons)

    return {"pass": decision, "score": score, "reasons": reasons, "checks": checks}


# ── Compatibility aliases (used by main.py imports) ──────────────────────────

def all_entry_filters_pass(side: str, indicators: dict) -> bool:
    """Legacy function: calls multi_layer_filter and returns bool."""
    result = multi_layer_filter(side, indicators)
    return result["pass"]


def parse_news_times(news_str: str) -> list:
    """Parse comma-separated news time strings like '08:30,13:30'."""
    times = []
    if not news_str:
        return times
    for t in news_str.split(","):
        t = t.strip()
        try:
            parts = t.split(":")
            times.append((int(parts[0]), int(parts[1])))
        except Exception:
            pass
    return times


def should_close_all_on_friday(*args, now=None) -> bool:
    """Returns True on Fridays after 20:00 UTC."""
    from datetime import datetime, timezone
    if now is None:
        now = datetime.now(timezone.utc)
    return now.weekday() == 4 and now.hour >= 20
