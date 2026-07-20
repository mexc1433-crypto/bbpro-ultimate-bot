"""
filters.py
==========
Entry-confirmation filters for the Bollinger Breakout bot.

Each filter returns True if the proposed trade is *allowed* by that filter.
Filters are intentionally decoupled from data sources so they can be
unit-tested in isolation.

Filters implemented:
    1. RSI momentum filter        - blocks buys in overbought, sells in oversold
    2. EMA trend filter           - blocks trades against EMA50/EMA200 alignment
    3. Volatility (ATR) filter    - blocks trades when ATR is out of range
    4. Session / time-of-day      - blocks trades outside configured sessions
    5. News blackout filter       - blocks trades around manual news times
    6. Friday flat-close          - closes everything Friday evening (UTC)
"""

from datetime import datetime, time, timedelta, timezone
from typing import List, Optional

from config import BotConfig, TradeDirection


# ===========================================================================
#  1. RSI MOMENTUM FILTER
# ===========================================================================
def rsi_filter_passes(cfg: BotConfig, direction: TradeDirection,
                      rsi_value: float) -> bool:
    """
    For BUY  : block if RSI >= overbought  (don't chase an overbought market)
    For SELL : block if RSI <= oversold    (don't chase an oversold market)
    """
    if not cfg.enable_rsi_filter:
        return True

    if direction == TradeDirection.BUY:
        if rsi_value >= cfg.rsi_overbought:
            return False
    else:  # SELL
        if rsi_value <= cfg.rsi_oversold:
            return False
    return True


# ===========================================================================
#  2. EMA TREND FILTER
# ===========================================================================
def trend_filter_passes(cfg: BotConfig, direction: TradeDirection,
                        price: float, ema_fast: float,
                        ema_slow: float) -> bool:
    """
    For BUY : price must be above EMA50  (and above EMA200 + EMA50>EMA200 if strict)
    For SELL: price must be below EMA50  (and below EMA200 + EMA50<EMA200 if strict)
    """
    if not cfg.enable_trend_filter:
        return True

    if direction == TradeDirection.BUY:
        ok = price > ema_fast
        if cfg.require_both_emas:
            ok = ok and (price > ema_slow) and (ema_fast > ema_slow)
        return ok
    else:  # SELL
        ok = price < ema_fast
        if cfg.require_both_emas:
            ok = ok and (price < ema_slow) and (ema_fast < ema_slow)
        return ok


# ===========================================================================
#  3. ATR VOLATILITY FILTER
# ===========================================================================
def volatility_filter_passes(cfg: BotConfig, atr_pips: float) -> bool:
    """Allow trade only if ATR is within [min, max] window (0 = disabled)."""
    if cfg.min_atr_pips > 0 and atr_pips < cfg.min_atr_pips:
        return False
    if cfg.max_atr_pips > 0 and atr_pips > cfg.max_atr_pips:
        return False
    return True


# ===========================================================================
#  4. SESSION / TIME FILTER  (UTC)
# ===========================================================================
def _is_asian_session(hour: int) -> bool:
    """Asian session: 23:00 - 08:00 UTC (wraps midnight)."""
    return hour >= 23 or hour < 8


def _is_london_session(hour: int) -> bool:
    """London session: 07:00 - 16:00 UTC."""
    return 7 <= hour < 16


def _is_new_york_session(hour: int) -> bool:
    """New York session: 12:00 - 21:00 UTC."""
    return 12 <= hour < 21


def _is_overlap(hour: int) -> bool:
    """London-NY overlap: 12:00 - 16:00 UTC (highest liquidity)."""
    return 12 <= hour < 16


def session_filter_passes(cfg: BotConfig, now_utc: datetime) -> bool:
    if not cfg.enable_session_filter:
        return True

    hour = now_utc.hour
    dow  = now_utc.weekday()  # Mon=0 ... Sun=6

    # Weekend block: Saturday fully, Sunday before 21:00 UTC
    if dow == 5:  # Saturday
        return False
    if dow == 6 and hour < 21:  # Sunday pre-open
        return False

    # Friday filter
    if dow == 4 and not cfg.trade_on_friday:
        return False

    # Overlap-only mode overrides individual session toggles
    if cfg.only_overlap:
        return _is_overlap(hour)

    if cfg.allow_asian    and _is_asian_session(hour):    return True
    if cfg.allow_london   and _is_london_session(hour):   return True
    if cfg.allow_new_york and _is_new_york_session(hour): return True
    return False


# ===========================================================================
#  5. NEWS BLACKOUT FILTER (manual UTC times)
# ===========================================================================
def parse_news_times(raw_list: List[str]) -> List[datetime]:
    """Parse ['2026-07-07 12:30', ...] into timezone-aware UTC datetimes."""
    out: List[datetime] = []
    for s in raw_list:
        s = s.strip()
        if not s:
            continue
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                out.append(dt)
                break
            except ValueError:
                continue
        else:
            print(f"[NewsFilter] WARNING: could not parse '{s}'")
    return out


def news_filter_passes(cfg: BotConfig, parsed_news: List[datetime],
                       now_utc: datetime) -> bool:
    """Returns True if trading is ALLOWED (i.e. NOT in blackout window)."""
    if not cfg.enable_news_filter or not parsed_news:
        return True
    for news_time in parsed_news:
        delta_min = (news_time - now_utc).total_seconds() / 60.0
        # Block from (news - before) to (news + after)
        if -cfg.news_block_after_min <= delta_min <= cfg.news_block_before_min:
            return False
    return True


# ===========================================================================
#  6. FRIDAY FLAT-CLOSE HELPER
# ===========================================================================
def should_close_all_on_friday(cfg: BotConfig, now_utc: datetime) -> bool:
    """True if it's Friday and past the configured flat-close time."""
    if not cfg.trade_on_friday:
        return False
    if now_utc.weekday() != 4:  # not Friday
        return False
    try:
        hh, mm = [int(x) for x in cfg.friday_close_time.split(":")]
        close_t = time(hh, mm)
        return now_utc.time() >= close_t
    except (ValueError, AttributeError):
        return False


# ===========================================================================
#  AGGREGATE: full entry check
# ===========================================================================
def all_entry_filters_pass(
    cfg: BotConfig,
    direction: TradeDirection,
    *,
    rsi_value: float,
    price: float,
    ema_fast: float,
    ema_slow: float,
    atr_pips: float,
    now_utc: datetime,
    parsed_news: List[datetime],
    debug: bool = False,
) -> bool:
    """Run all entry filters; return True only if every one passes."""
    reasons = []

    if not rsi_filter_passes(cfg, direction, rsi_value):
        reasons.append(f"RSI {rsi_value:.1f} blocks {direction.value}")

    if not trend_filter_passes(cfg, direction, price, ema_fast, ema_slow):
        reasons.append(f"Trend blocks {direction.value}")

    if not volatility_filter_passes(cfg, atr_pips):
        reasons.append(f"ATR {atr_pips:.1f} pips out of range")

    if not session_filter_passes(cfg, now_utc):
        reasons.append("Outside allowed session")

    if not news_filter_passes(cfg, parsed_news, now_utc):
        reasons.append("News blackout window")

    if reasons and debug:
        print(f"[Filters] {direction.value} blocked: {' | '.join(reasons)}")

    return len(reasons) == 0
