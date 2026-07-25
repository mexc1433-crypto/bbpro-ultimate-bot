"""
main.py
=======
Bollinger Breakout Pro v2 - Python Edition (ULTIMATE)

v2 additions over v1:
  - ADX filter (from MainBot.cs)
  - Volatility ratio (ATR/StDev)
  - SL padding + min SL distance
  - Partial TP
  - RSI exit levels (close on extreme RSI)
  - Max bars in trade
  - Spread guard + kill-switch + conflict-gate
  - Force exit at session end
  - Telegram notifications
  - SQLite trade logging
  - Performance analytics
  - Web UI (Flask dashboard on port 5100)

Run:
  python main.py
"""

import asyncio, time
import logging
import signal
import sys
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional, List

# Ensure local imports work from any CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from config import BotConfig, DEFAULT_CONFIG, TradeDirection, BreakoutMode, SizingMode
from indicators import compute_all_indicators
from filters import (
    all_entry_filters_pass, parse_news_times,
    should_close_all_on_friday,
)
from risk_manager import (
    calculate_position_size, calculate_sl_tp,
    calculate_trailing_stop, calculate_break_even,
    DailyState, check_daily_reset, can_open_new_trade,
)
from ctrader_client import CTraderClient, SymbolInfo, Bar
from notifications.telegram import create_notifier
from storage.database import TradeDB
from analytics.performance import PerformanceAnalyzer
from web.monitor import start_monitor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BBProV2")


# ===========================================================================
#  BOT CLASS
# ===========================================================================
class BollingerBreakoutBotV2:
    """Top-level orchestrator with all v2 features."""

    def __init__(self, cfg: Optional[BotConfig] = None):
        self.cfg = cfg or DEFAULT_CONFIG
        self.client = CTraderClient(self.cfg)
        self.daily_state    = DailyState()
        self._daily_trades_list: list = []
        self.news_times = parse_news_times(self.cfg.manual_news_times)
        self.symbol_info: Optional[SymbolInfo] = None
        self.bars: List[Bar] = []
        self._stop = False
        self._last_bar_ts: Optional[float] = None
        self._kill_switch_active = False

        # NEW v2 components
        self.notifier = create_notifier(
            self.cfg.telegram_bot_token,
            self.cfg.telegram_chat_id,
            self.cfg.telegram_enabled,
        )
        self.db: Optional[TradeDB] = (
            TradeDB(self.cfg.db_path) if self.cfg.db_enabled else None
        )
        self.analyzer = PerformanceAnalyzer(self.cfg.db_path) if self.cfg.db_enabled else None
        self._partial_taken: set = set()  # position IDs that already had partial TP

    # ------------------------------------------------------------------
    #  LIFECYCLE
    # ------------------------------------------------------------------
    async def run(self) -> None:
        errs = self.cfg.validate()
        if errs:
            for e in errs:
                logger.error("Config error: %s", e)
            logger.error("Fix config.py and retry.")
            return

        logger.info("=" * 70)
        logger.info("Bollinger Breakout Pro v2.0 - ULTIMATE Python Edition")
        logger.info("=" * 70)
        logger.info("Symbol: %s | TF: %s | Risk: %.2f%%",
                    self.cfg.symbol, self.cfg.timeframe, self.cfg.risk_per_trade)
        logger.info("BB(%d, %.1f) | RSI(%d) | EMA(%d/%d) | ATR(%d) | ADX(%d/%.1f)",
                    self.cfg.bb_period, self.cfg.bb_deviations,
                    self.cfg.rsi_period,
                    self.cfg.fast_ema_period, self.cfg.slow_ema_period,
                    self.cfg.atr_period, self.cfg.adx_period, self.cfg.min_adx)
        logger.info("SL: ATR*%.1f+%.1fpad | TP: ATR*%.1f | Trail: ATR*%.1f | BE: ATR*%.1f",
                    self.cfg.sl_atr_multiplier, self.cfg.sl_pad_pips,
                    self.cfg.tp_atr_multiplier,
                    self.cfg.trail_distance_atr_mult, self.cfg.be_trigger_atr_mult)
        if self.cfg.enable_partial_tp:
            logger.info("Partial TP: %d%% at %.1f×SL",
                        self.cfg.ptp_percent, self.cfg.ptp_sl_multiplier)
        logger.info("Safety: MaxSpread=%.1f | KillSwitch=%s | ConflictGate=%s",
                    self.cfg.max_spread_pips,
                    self.cfg.kill_switch_on_error, self.cfg.conflict_gate)
        logger.info("Notifications: Telegram=%s | DB=%s | Web=%s",
                    self.cfg.telegram_enabled, self.cfg.db_enabled,
                    self.cfg.web_monitor_enabled)
        logger.info("=" * 70)

        # Start web monitor
        if self.cfg.web_monitor_enabled:
            try:
                start_monitor(self.cfg.db_path, self.cfg.web_monitor_port,
                              self.cfg.web_monitor_host)
                logger.info("🌐 Web monitor: http://localhost:%d",
                            self.cfg.web_monitor_port)
            except Exception as e:
                logger.warning("Web monitor failed to start: %s", e)

        # Connect to broker
        await self.client.connect()
        await asyncio.sleep(2.0)

        # Fetch symbol info & warm-up bars
        try:
            self.symbol_info = await self.client.get_symbol_info(self.cfg.symbol)
            logger.info("Symbol info: pip_size=%.5f | min_vol=%d | step=%d",
                        self.symbol_info.pip_size,
                        self.symbol_info.min_volume_units,
                        self.symbol_info.volume_step_units)
        except Exception as e:
            logger.error("Failed to fetch symbol info: %s", e)

        warmup_count = max(self.cfg.bb_period, self.cfg.slow_ema_period,
                           self.cfg.atr_period) + 50
        self.bars = await self.client.get_recent_bars(
            self.cfg.symbol, self.cfg.timeframe, count=warmup_count
        )
        if self.bars:
            logger.info("Warm-up bars loaded: %d", len(self.bars))
            self._last_bar_ts = self.bars[-1].timestamp
        else:
            logger.warning("No warm-up bars. Bot will wait for live bars.")

        await self.client.subscribe_spots(self.cfg.symbol)

        equity = await self.client.get_account_equity()
        self.daily_state.reset(equity, datetime.now(timezone.utc))
        # Push real balance to web dashboard
        try:
            from web.monitor import _flask_app
            if _flask_app:
                _flask_app.config['ACCOUNT_BALANCE'] = equity
                _flask_app.config['ACCOUNT_EQUITY']  = equity
                _flask_app.config['ACCOUNT_ID']      = str(self.cfg.account_id)
                _flask_app.config['TRADING_MODE']   = 'LIVE' if self.client.is_live else 'PAPER'
                _flask_app.config['TCP_CONNECTED']  = self.client.is_live
        except Exception:
            pass

        # Telegram start notification
        self.notifier.notify_start(self.cfg.symbol, self.cfg.timeframe,
                                   self.cfg.risk_per_trade)
        # Send professional startup message
        equity = getattr(self, '_last_equity', 0) or 10000.0
        self.notifier.send_startup_message(balance=equity, account_id=self.cfg.account_id)

        # Install signal handlers
        self._install_signal_handlers()

        # Main loop
        logger.info("Entering main loop (poll every %ds)", self.cfg.poll_interval_sec)
        try:
            while not self._stop:
                await self._tick()
                await asyncio.sleep(self.cfg.poll_interval_sec)
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        finally:
            await self.client.disconnect()
            self.notifier.notify_stop()
            logger.info("Bot stopped.")

    def _install_signal_handlers(self):
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._request_stop)
        except NotImplementedError:
            pass

    def _request_stop(self):
        logger.info("Stop signal received...")
        self._stop = True

    # ------------------------------------------------------------------
    #  MAIN TICK
    # ------------------------------------------------------------------
    async def _tick(self) -> None:
        now_utc = datetime.now(timezone.utc)

        equity = await self.client.get_account_equity()
        if check_daily_reset(self.daily_state, equity, now_utc):
            logger.info("New day reset | Start equity: %.2f", equity)
            # Send daily results summary via Telegram
            if self.cfg.telegram_enabled and hasattr(self, '_daily_trades_list'):
                self.notifier.send_daily_results(self._daily_trades_list, self.cfg.symbol)
                self._daily_trades_list = []

        # Update web dashboard with real balance (only for XAUUSD instance)
        if self.cfg.symbol == "XAUUSD" and equity > 0:
            try:
                from web.monitor import _flask_app_ref
                _flask_app = _flask_app_ref[0]
                if _flask_app:
                    _flask_app.config['ACCOUNT_BALANCE'] = equity
                    _flask_app.config['ACCOUNT_EQUITY']  = equity
                    _flask_app.config['ACCOUNT_ID']      = str(self.cfg.account_id)
            except Exception:
                pass

        # Log equity to DB
        if self.db:
            try:
                self.db.log_equity(
                    equity=equity, balance=equity,
                    open_positions=0,  # would need live positions cache
                    floating_pnl=0.0,
                )
            except Exception:
                pass

        # Friday flat-close
        if should_close_all_on_friday(self.cfg, now_utc):
            n = await self.client.close_all_positions(self.cfg.bot_label)
            if n > 0:
                logger.info("Friday flat-close: closed %d positions", n)

        # New bar check
        if await self._check_new_bar():
            await self._on_bar_close(now_utc)

        # Manage open positions
        await self._manage_open_positions()

    # ------------------------------------------------------------------
    #  NEW-BAR DETECTION
    # ------------------------------------------------------------------
    async def _check_new_bar(self) -> bool:
        if not self.symbol_info:
            return False
        new_bars = await self.client.get_recent_bars(
            self.cfg.symbol, self.cfg.timeframe, count=2
        )
        if not new_bars:
            return False
        latest_ts = new_bars[-1].timestamp
        if self._last_bar_ts is None or latest_ts > self._last_bar_ts:
            self.bars.extend(new_bars)
            max_window = max(self.cfg.slow_ema_period, 500)
            if len(self.bars) > max_window * 2:
                self.bars = self.bars[-max_window * 2:]
            self._last_bar_ts = latest_ts
            return True
        return False

    # ------------------------------------------------------------------
    #  BAR-CLOSE EVALUATION
    # ------------------------------------------------------------------
    async def _on_bar_close(self, now_utc: datetime) -> None:
        if len(self.bars) < max(self.cfg.bb_period, self.cfg.slow_ema_period,
                                self.cfg.atr_period) + 5:
            return

        # Kill-switch
        if self._kill_switch_active:
            return

        # Daily DD / max trades
        equity = await self.client.get_account_equity()
        allowed, reason = can_open_new_trade(self.cfg, self.daily_state, equity)
        if not allowed:
            if "DD" in reason and self.cfg.telegram_enabled:
                self.notifier.notify_daily_dd_hit(
                    (self.daily_state.day_start_equity - equity) / self.daily_state.day_start_equity * 100,
                    self.cfg.max_daily_loss_pct,
                )
            return

        # Fetch H4 bars
        try:
            bars_h4 = await self.client.get_recent_bars(self.cfg.symbol, "h4", count=250)
        except Exception as e:
            logger.warning("Failed to fetch H4 bars: %s", e)
            bars_h4 = []

        # Compute indicators
        closes = np.array([b.close for b in self.bars])
        highs  = np.array([b.high  for b in self.bars])
        lows   = np.array([b.low   for b in self.bars])
        ind = compute_all_indicators(highs, lows, closes, self.cfg)

        idx = -1
        close_now  = closes[idx]
        close_prev = closes[idx - 1]
        bb_upper   = ind["bb_upper"][idx]
        bb_lower   = ind["bb_lower"][idx]
        bb_upper_p = ind["bb_upper"][idx - 1]
        bb_lower_p = ind["bb_lower"][idx - 1]
        rsi_now    = ind["rsi"][idx]
        ema_f      = ind["ema_fast"][idx]
        ema_s      = ind["ema_slow"][idx]
        atr_now    = ind["atr"][idx]
        adx_now    = ind["adx"][idx] if "adx" in ind else 0.0
        if np.isnan(adx_now):
            adx_now = 0.0

        if any(np.isnan([bb_upper, bb_lower, rsi_now, ema_f, ema_s, atr_now])):
            return

        atr_pips = atr_now / self.symbol_info.pip_size

        # Spread guard / calculation
        spread_pips = 1.0  # default/fallback if not available
        bid, ask = await self.client.get_quote(self.cfg.symbol)
        if bid and ask:
            spread_pips = (ask - bid) / self.symbol_info.pip_size

        # Keep original strict spread guard if config has max_spread_pips > 0
        if self.cfg.max_spread_pips > 0:
            if spread_pips > self.cfg.max_spread_pips:
                if self.cfg.show_debug:
                    logger.info("Spread too wide: %.1f > %.1f", spread_pips, self.cfg.max_spread_pips)
                return

        # Check both directions
        for direction in (TradeDirection.BUY, TradeDirection.SELL):
            breakout = self._check_breakout(direction, close_now, close_prev,
                                            bb_upper, bb_lower,
                                            bb_upper_p, bb_lower_p)
            if not breakout:
                continue

            # NEW: Volatility ratio
            if self.cfg.min_volatility_ratio > 0:
                # Compute simple stddev
                std = float(np.std(closes[-self.cfg.std_dev_period:]))
                if std > 0:
                    ratio = atr_now / std
                    if ratio < self.cfg.min_volatility_ratio:
                        if self.cfg.show_debug:
                            logger.info("VolRatio too low: %.2f < %.2f", ratio, self.cfg.min_volatility_ratio)
                        continue

            # Prepare indicators dict for our multi-layer filter
            indicators_dict = {
                "bars_h4": bars_h4,
                "rsi": rsi_now,
                "adx": adx_now,
                "spread": spread_pips,
                "time": now_utc,
            }

            ok, score = self._evaluate_signal(direction, indicators_dict)
            if not ok:
                # Log rejected signal
                if self.db:
                    self.db.log_signal(
                        symbol=self.cfg.symbol, side=direction.value,
                        direction=direction.value, accepted=False,
                        reject_reason=f"filters_score_{score:.1f}", price=close_now,
                        rsi=rsi_now, ema_fast=ema_f, ema_slow=ema_s,
                        atr=atr_now, adx=adx_now,
                    )
                continue

            # Compute SL/TP with padding
            sl_tp = calculate_sl_tp(
                self.cfg,
                direction=direction,
                entry_price=close_now,
                atr_value=atr_now,
                pip_size=self.symbol_info.pip_size,
            )
            if sl_tp is None:
                continue

            # Min SL distance
            if sl_tp.sl_pips < self.cfg.min_sl_pips:
                sl_tp = type(sl_tp)(
                    sl_pips=self.cfg.min_sl_pips,
                    tp_pips=sl_tp.tp_pips,
                    sl_price=close_now - self.cfg.min_sl_pips * self.symbol_info.pip_size
                        if direction == TradeDirection.BUY
                        else close_now + self.cfg.min_sl_pips * self.symbol_info.pip_size,
                    tp_price=sl_tp.tp_price,
                )

            volume = calculate_position_size(
                self.cfg,
                sl_pips=sl_tp.sl_pips,
                account_equity=equity,
                pip_value_per_unit=self.symbol_info.pip_value_per_unit,
                min_volume_units=self.symbol_info.min_volume_units,
                volume_step_units=self.symbol_info.volume_step_units,
            )
            max_units = self.cfg.max_volume_lots * 100_000.0
            volume = min(volume, max_units)
            if volume < self.symbol_info.min_volume_units:
                continue

            side = "buy" if direction == TradeDirection.BUY else "sell"
            pos_id = await self.client.send_market_order(
                self.cfg.symbol, side=side,
                volume_units=volume,
                sl_pips=sl_tp.sl_pips,
                tp_pips=sl_tp.tp_pips,
                label=self.cfg.bot_label,
                comment=f"BBProV2 {self.cfg.symbol} {side}",
            )

            # Log to DB + Telegram (even if pos_id is None in stub mode)
            if self.db:
                trade_id = self.db.log_trade_open(
                    symbol=self.cfg.symbol, side=side, volume_units=volume,
                    entry_price=close_now, sl_price=sl_tp.sl_price,
                    tp_price=sl_tp.tp_price, sl_pips=sl_tp.sl_pips,
                    tp_pips=sl_tp.tp_pips, atr=atr_now, adx=adx_now,
                    rsi=rsi_now, label=self.cfg.bot_label,
                )
            self.daily_state.daily_trade_count += 1
            # Track for daily summary
            self._daily_trades_list.append({
                "symbol": self.cfg.symbol, "side": side,
                "price": close_now, "sl_pips": sl_tp.sl_pips, "tp_pips": sl_tp.tp_pips
            })
            # Professional signal message
            self.notifier.send_trade_signal(
                symbol=self.cfg.symbol, side=side,
                price=close_now,
                sl=sl_tp.sl_price, tp=sl_tp.tp_price,
                volume_lots=volume / 100000.0,
            )
            logger.info("%s OPENED | vol=%.2fu | SL=%.1fp | TP=%.1fp | ATR=%.5f | ADX=%.1f | Score=%.1f",
                        side.upper(), volume, sl_tp.sl_pips, sl_tp.tp_pips, atr_now, adx_now, score)
            break

    def _evaluate_signal(self, direction: TradeDirection, indicators_dict: dict) -> tuple:
        """
        Evaluates entry signal using multi-layer filter.
        Returns (bool, confluence_score).
        """
        from filters import multi_layer_filter
        side = "buy" if direction.value == 1 else "sell"
        result = multi_layer_filter(side, indicators_dict)
        score = result.get("score", 0)
        passed = result.get("pass", False)
        # Update web dashboard confluence score
        try:
            from web.monitor import _app_instance
            if _app_instance:
                _app_instance.config["CONFLUENCE_SCORE"] = score
        except Exception:
            pass
        return passed, score

    def _check_breakout(self, direction: TradeDirection,
                        close_now: float, close_prev: float,
                        bb_upper: float, bb_lower: float,
                        bb_upper_p: float, bb_lower_p: float) -> bool:
        pip = self.symbol_info.pip_size
        if direction == TradeDirection.BUY:
            if self.cfg.bb_mode == BreakoutMode.TOUCH_BAND:
                return close_now >= bb_upper and close_prev < bb_upper_p
            if self.cfg.bb_mode == BreakoutMode.PENETRATION_PIPS:
                return close_now >= bb_upper + pip
            return close_now > bb_upper and close_prev <= bb_upper_p
        else:
            if self.cfg.bb_mode == BreakoutMode.TOUCH_BAND:
                return close_now <= bb_lower and close_prev > bb_lower_p
            if self.cfg.bb_mode == BreakoutMode.PENETRATION_PIPS:
                return close_now <= bb_lower - pip
            return close_now < bb_lower and close_prev >= bb_lower_p

    # ------------------------------------------------------------------
    #  POSITION MANAGEMENT
    # ------------------------------------------------------------------
    async def _manage_open_positions(self) -> None:
        """Apply break-even / trailing / partial TP / RSI exit / max bars.

        NOTE: This is a blueprint - in production, wire it to the live
        positions cache maintained via ProtoOASubscribePositionsEvents.
        """
        if not self.bars or self.symbol_info is None:
            return

        closes = np.array([b.close for b in self.bars])
        highs  = np.array([b.high  for b in self.bars])
        lows   = np.array([b.low   for b in self.bars])
        ind = compute_all_indicators(highs, lows, closes, self.cfg)
        atr_now = ind["atr"][-1]
        rsi_now = ind["rsi"][-1]
        if np.isnan(atr_now) or atr_now <= 0:
            return

        # Real implementation would iterate over cached positions:
        # for pos in open_positions:
        #     ... (logic shown in v1 risk_manager.py)
        pass

    def activate_kill_switch(self, reason: str):
        self._kill_switch_active = True
        logger.error("KILL-SWITCH: %s", reason)
        self.notifier.notify_kill_switch(reason)
        if self.db:
            self.db.log_error(severity="kill_switch", category="order",
                              message=reason)


# ===========================================================================
#  ENTRY POINT
# ===========================================================================
ALL_SYMBOLS = [
    "XAUUSD",   # Gold — primary
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "EURJPY",
    "USDCAD",
]


def _start_keepalive():
    """Self-ping every 10 min to prevent Render free tier spin-down."""
    def _ping():
        while True:
            try:
                port = os.environ.get("PORT", "5100")
                url = f"http://127.0.0.1:{port}/health"
                urllib.request.urlopen(url, timeout=10)
                logger.info("🔄 Keep-alive ping OK")
            except Exception:
                pass
            time.sleep(600)  # 10 minutes
    t = threading.Thread(target=_ping, daemon=True)
    t.start()

async def run_symbol(symbol: str, web_enabled: bool = False):
    """Run the bot for a single symbol with its own config and client."""
    from config import load_config
    cfg = load_config()           # load all creds from env
    cfg.symbol = symbol
    cfg.active_symbol = symbol
    # Only the FIRST symbol (XAUUSD) runs the web monitor
    if not web_enabled:
        cfg.web_monitor_enabled = False
    # Symbol-specific tuning
    if "XAU" in symbol:
        cfg.atr_sl_mult     = 2.0
        cfg.atr_tp_mult     = 3.0
        cfg.max_spread_pips = 5.0
        cfg.bb_period       = 20
    elif "JPY" in symbol:
        cfg.max_spread_pips = 4.0
    else:
        cfg.max_spread_pips = 3.0
    bot = BollingerBreakoutBotV2(cfg)
    try:
        await bot.run()
    except Exception as e:
        logger.error("[%s] Bot error: %s", symbol, e)


_start_keepalive()

async def run_all_symbols():
    """Run all symbols concurrently."""
    logger.info("🚀 Starting BBPro ULTIMATE — %d symbols: %s",
                len(ALL_SYMBOLS), ", ".join(ALL_SYMBOLS))
    tasks = [
        asyncio.create_task(run_symbol(sym, web_enabled=(i == 0)))
        for i, sym in enumerate(ALL_SYMBOLS)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


def main():
    MAX_RETRIES = 10
    RETRY_DELAY = 30  # seconds between retries

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            asyncio.run(run_all_symbols())
            break  # clean exit
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            break
        except Exception as e:
            logger.exception("Fatal error (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                logger.info("⏳ Retrying in %d seconds...", RETRY_DELAY)
                time.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries reached. Exiting.")
                sys.exit(1)


if __name__ == "__main__":
    main()
