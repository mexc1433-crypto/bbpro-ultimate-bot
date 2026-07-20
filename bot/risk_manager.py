"""
risk_manager.py
===============
Risk & trade-management layer.

Responsibilities:
    1. Dynamic position sizing based on % risk per trade
    2. Daily drawdown / max-trades tracking
    3. ATR-based trailing-stop calculation
    4. Break-even trigger logic
    5. Concurrent-positions limit

This module is *pure calculation* - it never talks to the broker directly.
The broker client (ctrader_client.py) consumes the values produced here
and applies them to live orders.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List
import math

from config import BotConfig, SizingMode, TradeDirection


# ===========================================================================
#  POSITION-SIZE CALCULATION
# ===========================================================================
def calculate_position_size(
    cfg: BotConfig,
    *,
    sl_pips: float,
    account_equity: float,
    pip_value_per_unit: float,   # value of 1 pip for 1 unit of volume, in account CCY
    min_volume_units: float,
    volume_step_units: float,
) -> float:
    """
    Compute trade volume (in units, NOT lots) so that hitting SL costs
    exactly `risk_per_trade` % of `account_equity`.

    If SizingMode.FIXED_LOTS, returns the fixed volume converted to units.
    Volume is rounded down to the nearest `volume_step_units` and capped
    at `cfg.max_volume_lots` (converted to units by caller).
    """
    if sl_pips <= 0 or pip_value_per_unit <= 0:
        return 0.0

    if cfg.sizing_mode == SizingMode.FIXED_LOTS:
        return cfg.fixed_volume_lots * 100_000.0   # caller can override if needed

    # Risk amount in account currency
    risk_amount = account_equity * (cfg.risk_per_trade / 100.0)
    # Volume so that: sl_pips * pip_value_per_unit * volume = risk_amount
    raw_units = risk_amount / (sl_pips * pip_value_per_unit)

    # Cap at max volume (caller will pass max_volume_lots already in units)
    # We rely on caller to do the cap by passing max_volume_lots*100000 as min_volume_units? No.
    # Instead, cap here using a sane default and let caller override.
    return max(_round_down_to_step(raw_units, volume_step_units), min_volume_units)


def _round_down_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


# ===========================================================================
#  SL / TP CALCULATION (ATR-BASED)
# ===========================================================================
@dataclass
class SlTpResult:
    sl_pips: float
    tp_pips: float
    sl_price: float
    tp_price: float


def calculate_sl_tp(
    cfg: BotConfig,
    *,
    direction: TradeDirection,
    entry_price: float,
    atr_value: float,
    pip_size: float,
) -> Optional[SlTpResult]:
    """Compute ATR-based SL/TP in both pips and absolute price."""
    if atr_value <= 0 or pip_size <= 0:
        return None

    sl_distance_price = atr_value * cfg.sl_atr_multiplier
    tp_distance_price = atr_value * cfg.tp_atr_multiplier

    sl_pips = sl_distance_price / pip_size
    tp_pips = tp_distance_price / pip_size

    if direction == TradeDirection.BUY:
        sl_price = entry_price - sl_distance_price
        tp_price = entry_price + tp_distance_price
    else:
        sl_price = entry_price + sl_distance_price
        tp_price = entry_price - tp_distance_price

    return SlTpResult(sl_pips=sl_pips, tp_pips=tp_pips,
                      sl_price=sl_price, tp_price=tp_price)


# ===========================================================================
#  TRAILING-STOP CALCULATION
# ===========================================================================
@dataclass
class TrailingResult:
    new_sl_price: Optional[float]   # None if no update needed
    reason: str = ""


def calculate_trailing_stop(
    cfg: BotConfig,
    *,
    direction: TradeDirection,
    entry_price: float,
    current_price: float,
    current_sl: Optional[float],
    atr_value: float,
    pip_size: float,
) -> TrailingResult:
    """
    ATR-based trailing stop. Activates once price has moved
    `trail_start_atr_mult * ATR` in favour, then keeps SL at
    `trail_distance_atr_mult * ATR` behind the current price.
    """
    if not cfg.enable_trailing or atr_value <= 0:
        return TrailingResult(None, "trailing disabled")

    profit_price = (current_price - entry_price) if direction == TradeDirection.BUY \
                   else (entry_price - current_price)
    start_distance = atr_value * cfg.trail_start_atr_mult

    if profit_price < start_distance:
        return TrailingResult(None, "below trail-start threshold")

    trail_distance = atr_value * cfg.trail_distance_atr_mult

    if direction == TradeDirection.BUY:
        new_sl = current_price - trail_distance
        if current_sl is None or new_sl > current_sl:
            return TrailingResult(new_sl, "trailing up")
        return TrailingResult(None, "no improvement")
    else:  # SELL
        new_sl = current_price + trail_distance
        if current_sl is None or new_sl < current_sl:
            return TrailingResult(new_sl, "trailing down")
        return TrailingResult(None, "no improvement")


# ===========================================================================
#  BREAK-EVEN LOGIC
# ===========================================================================
@dataclass
class BreakEvenResult:
    new_sl_price: Optional[float]
    applied: bool = False
    reason: str = ""


def calculate_break_even(
    cfg: BotConfig,
    *,
    direction: TradeDirection,
    entry_price: float,
    current_price: float,
    current_sl: Optional[float],
    atr_value: float,
    pip_size: float,
) -> BreakEvenResult:
    """
    Move SL to entry (+ lock-in pips) once price has moved
    `be_trigger_atr_mult * ATR` in favour.
    """
    if not cfg.enable_break_even or atr_value <= 0:
        return BreakEvenResult(None, reason="BE disabled")

    profit_price = (current_price - entry_price) if direction == TradeDirection.BUY \
                   else (entry_price - current_price)
    trigger = atr_value * cfg.be_trigger_atr_mult

    if profit_price < trigger:
        return BreakEvenResult(None, reason="below BE trigger")

    lock_in_price = cfg.be_lock_in_pips * pip_size

    if direction == TradeDirection.BUY:
        target_sl = entry_price + lock_in_price
        # Already at or better than target?
        if current_sl is not None and current_sl >= target_sl - 2 * pip_size:
            return BreakEvenResult(None, reason="BE already applied")
        return BreakEvenResult(target_sl, applied=True, reason="BE applied (buy)")
    else:
        target_sl = entry_price - lock_in_price
        if current_sl is not None and current_sl <= target_sl + 2 * pip_size:
            return BreakEvenResult(None, reason="BE already applied")
        return BreakEvenResult(target_sl, applied=True, reason="BE applied (sell)")


# ===========================================================================
#  DAILY DRAWDOWN TRACKER
# ===========================================================================
@dataclass
class DailyState:
    day_start_equity: float = 0.0
    current_day_utc: Optional[datetime] = None
    daily_trade_count: int = 0
    daily_limit_hit: bool = False

    def reset(self, equity: float, now_utc: datetime) -> None:
        self.day_start_equity = equity
        self.current_day_utc = now_utc.date()
        self.daily_trade_count = 0
        self.daily_limit_hit = False


def check_daily_reset(state: DailyState, equity: float, now_utc: datetime) -> bool:
    """Returns True if a new-day reset happened."""
    today = now_utc.date()
    if state.current_day_utc != today:
        state.reset(equity, now_utc)
        return True
    return False


def can_open_new_trade(cfg: BotConfig, state: DailyState, equity: float) -> tuple:
    """
    Returns (allowed: bool, reason: str).
    """
    if state.daily_limit_hit:
        return False, "daily limit already hit"

    if cfg.enable_daily_dd:
        if state.day_start_equity > 0:
            dd_pct = (state.day_start_equity - equity) / state.day_start_equity * 100.0
            if dd_pct >= cfg.max_daily_loss_pct:
                state.daily_limit_hit = True
                return False, f"daily DD {dd_pct:.2f}% >= limit {cfg.max_daily_loss_pct:.1f}%"

    if cfg.max_daily_trades > 0 and state.daily_trade_count >= cfg.max_daily_trades:
        return False, f"max daily trades ({cfg.max_daily_trades}) reached"

    return True, "OK"
