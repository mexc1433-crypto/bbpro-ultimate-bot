"""
notifications/telegram.py
=========================
Telegram notification module for the Bollinger Breakout Pro bot.

Sends instant alerts to a Telegram chat when:
  - A trade is opened
  - A trade is closed (with P/L)
  - Break-even is applied
  - Trailing stop is updated
  - Partial TP is taken
  - Daily DD limit is hit
  - Kill-switch is activated
  - Bot starts / stops

Setup:
  1. Talk to @BotFather on Telegram → /newbot → get the bot token
  2. Send any message to your new bot
  3. Open https://api.telegram.org/bot<TOKEN>/getUpdates → copy chat.id
  4. Set values in config.py:
       telegram_bot_token: str = "123456:ABC-..."
       telegram_chat_id:   str = "987654321"
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends messages to a Telegram chat using the Bot API."""

    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token) and bool(chat_id)
        if self.enabled:
            logger.info("Telegram notifier enabled (chat_id=%s)", chat_id)
        else:
            logger.info("Telegram notifier disabled")

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a text message. Returns True on success."""
        if not self.enabled:
            return False
        url = self.API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code != 200:
                logger.warning("Telegram send failed: %s %s", r.status_code, r.text[:200])
                return False
            return True
        except Exception as e:
            logger.warning("Telegram send error: %s", e)
            return False

    # Convenience helpers ------------------------------------------------

    def notify_start(self, symbol: str, timeframe: str, risk_pct: float):
        self.send(
            f"🤖 <b>BBPro v2 Started</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Timeframe: <code>{timeframe}</code>\n"
            f"Risk/trade: <code>{risk_pct:.2f}%</code>"
        )

    def notify_stop(self):
        self.send("🛑 <b>BBPro v2 Stopped</b>")

    def notify_trade_opened(self, side: str, symbol: str, volume: float,
                            sl_pips: float, tp_pips: float, atr: float, adx: Optional[float] = None):
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        adx_str = f"\nADX: <code>{adx:.1f}</code>" if adx is not None else ""
        self.send(
            f"{emoji} <b>{side.upper()} OPENED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Volume: <code>{volume:.2f} units</code>\n"
            f"SL: <code>{sl_pips:.1f} pips</code>\n"
            f"TP: <code>{tp_pips:.1f} pips</code>\n"
            f"ATR: <code>{atr:.5f}</code>{adx_str}"
        )

    def notify_trade_closed(self, side: str, symbol: str, pips: float,
                            profit: float, reason: str = ""):
        emoji = "✅" if profit >= 0 else "❌"
        self.send(
            f"{emoji} <b>{side.upper()} CLOSED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Pips: <code>{pips:+.1f}</code>\n"
            f"P/L: <code>{profit:+.2f}</code>\n"
            f"Reason: <code>{reason or 'TP/SL'}</code>"
        )

    def notify_break_even(self, position_id: int, new_sl: float):
        self.send(f"🔒 <b>Break-Even</b> #{position_id}\nNew SL: <code>{new_sl:.5f}</code>")

    def notify_trailing(self, position_id: int, new_sl: float):
        # Trailing updates are noisy - only send if debug wanted
        pass

    def notify_partial_tp(self, position_id: int, pct: int, vol: float, pips: float):
        self.send(
            f"💰 <b>Partial TP</b> #{position_id}\n"
            f"Closed: <code>{pct}%</code> (<code>{vol:.2f}u</code>)\n"
            f"At: <code>{pips:+.1f} pips</code>"
        )

    def notify_daily_dd_hit(self, dd_pct: float, max_pct: float):
        self.send(
            f"🛑 <b>DAILY DD LIMIT</b>\n"
            f"Current: <code>{dd_pct:.2f}%</code>\n"
            f"Max: <code>{max_pct:.1f}%</code>\n"
            f"Bot stopped for today."
        )

    def notify_kill_switch(self, reason: str):
        self.send(f"⚠️ <b>KILL-SWITCH ACTIVATED</b>\nReason: <code>{reason}</code>")

    def notify_session_block(self, session: str):
        self.send(f"⏰ <b>Session filter</b> - outside <code>{session}</code>")

    def notify_news_block(self, news_time: str):
        self.send(f"📰 <b>News blackout</b> - around <code>{news_time}</code>")


# ===========================================================================
#  STUB FALLBACK (when requests is not installed)
# ===========================================================================
class StubNotifier:
    """Fallback that just logs messages instead of sending them."""
    def __init__(self, *args, **kwargs):
        logger.info("Telegram notifier in STUB mode (requests not installed)")

    def send(self, text: str, **kw) -> bool:
        logger.info("[TELEGRAM STUB] %s", text)
        return True

    def __getattr__(self, name):
        # Forward any notify_* call to send
        def _stub(*args, **kwargs):
            logger.info("[TELEGRAM STUB] %s: %s", name, args)
        return _stub


def create_notifier(bot_token: str, chat_id: str, enabled: bool = True):
    """Factory that returns TelegramNotifier or StubNotifier."""
    try:
        import requests  # noqa
        return TelegramNotifier(bot_token, chat_id, enabled)
    except ImportError:
        return StubNotifier()
