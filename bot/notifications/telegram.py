"""
notifications/telegram.py - BBPro Ultimate v2
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class TelegramNotifier:
    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token) and bool(chat_id) and HAS_REQUESTS
        if self.enabled:
            logger.info("Telegram notifier enabled (chat_id=%s)", chat_id)
        else:
            logger.info("Telegram notifier disabled")

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        url = self.API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {"chat_id": self.chat_id, "text": text,
                   "parse_mode": parse_mode, "disable_web_page_preview": True}
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code != 200:
                logger.warning("Telegram send failed: %s %s", r.status_code, r.text[:200])
                return False
            return True
        except Exception as e:
            logger.warning("Telegram send error: %s", e)
            return False

    def format_price(self, symbol: str, value) -> str:
        if not isinstance(value, (int, float)):
            return str(value)
        su = symbol.upper()
        if any(k in su for k in ["JPY", "XAU", "GOLD"]):
            return f"{value:.2f}"
        return f"{value:.4f}"

    def format_pip(self, val: float) -> str:
        try:
            v = float(val)
            return f"{int(v)}" if v == int(v) else f"{v:.1f}"
        except:
            return str(val)

    # ── Professional signal message ─────────────────────────────────────
    def send_trade_signal(self, symbol: str, side: str, price: float,
                          sl: float, tp: float, volume_lots: float) -> bool:
        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        side_str = "BUY" if side.upper() == "BUY" else "SELL"
        lots = volume_lots / 100000.0 if volume_lots > 100 else volume_lots
        times = max(1, int(round(lots / 0.01)))
        msg = (
            f"{emoji} {symbol.upper()} {side_str} NOW {self.format_price(symbol, price)} ✅\n"
            f"SL : {self.format_price(symbol, sl)}\n"
            f"TP : {self.format_price(symbol, tp)}\n"
            f"⚡ {times} مرات بلوت عالي"
        )
        return self.send(msg, parse_mode="HTML")

    # ── Daily results message ────────────────────────────────────────────
    def send_daily_results(self, trades_list: list, symbol: str) -> bool:
        total_win = 0.0
        total_loss = 0.0
        lines = []
        for t in trades_list:
            pips = float(t.get("pips", 0) if isinstance(t, dict) else getattr(t, "pips", 0) or 0)
            if pips >= 0:
                total_win += pips
                lines.append(f"+{self.format_pip(pips)} pip ✅")
            else:
                total_loss += abs(pips)
                lines.append(f"-{self.format_pip(abs(pips))} pip ✖️")
        if not lines:
            trades_str = "لا توجد صفقات اليوم"
        else:
            trades_str = "\n".join(lines)
        net = total_win - total_loss
        net_fmt = f"+{self.format_pip(net)}" if net >= 0 else f"-{self.format_pip(abs(net))}"
        emoji_net = "✅" if net >= 0 else "✖️"
        msg = (
            f"نتايج SCALPING {symbol.upper()} لليوم 🌟 :\n"
            f"{trades_str}\n\n"
            f"كل الصفقات بهدف واحد فقط….\n\n"
            f"توتال الخسارة لليوم : {self.format_pip(total_loss)} pip ✖️\n"
            f"توتال المكسب لليوم : {self.format_pip(total_win)} pip ✅\n\n"
            f"صافي مكسب لليوم : {net_fmt} pip {emoji_net}"
        )
        return self.send(msg, parse_mode="HTML")

    # ── Startup message ──────────────────────────────────────────────────
    def send_startup_message(self, balance, account_id) -> bool:
        bal_str = f"{int(balance):,} EUR" if isinstance(balance, (int, float)) else str(balance)
        now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"🤖 BBPro v2 — نشط الآن\n"
            f"💰 رصيد الحساب: {bal_str}\n"
            f"📊 حساب تجريبي #{account_id}\n"
            f"⚙️ الأزواج: XAUUSD · EURUSD · GBPUSD · USDJPY · EURJPY · USDCAD\n"
            f"🕐 الوقت: {now_utc}"
        )
        return self.send(msg, parse_mode="HTML")

    # ── Standard trade notifications ─────────────────────────────────────
    def notify_start(self, symbol, timeframe, risk_pct):
        self.send(f"🤖 <b>BBPro v2 Started</b>\nSymbol: <code>{symbol}</code>\nTF: <code>{timeframe}</code>\nRisk: <code>{risk_pct:.2f}%</code>")

    def notify_stop(self):
        self.send("🛑 <b>BBPro v2 Stopped</b>")

    def notify_trade_opened(self, side, symbol, volume, sl_pips, tp_pips, atr, adx=None):
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        self.send_trade_signal(symbol, side, 0.0, sl_pips, tp_pips, volume)

    def notify_trade_closed(self, side, symbol, pips, profit, reason=""):
        emoji = "✅" if profit >= 0 else "❌"
        self.send(
            f"{emoji} <b>{side.upper()} CLOSED</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Pips: <code>{pips:+.1f}</code>\n"
            f"P/L: <code>{profit:+.2f}</code>\n"
            f"Reason: <code>{reason or 'TP/SL'}</code>"
        )

    def notify_break_even(self, position_id, new_sl):
        self.send(f"🔒 <b>Break-Even</b> #{position_id}\nNew SL: <code>{new_sl:.5f}</code>")

    def notify_trailing(self, position_id, new_sl): pass

    def notify_partial_tp(self, position_id, pct, vol, pips):
        self.send(f"💰 <b>Partial TP</b> #{position_id}\nClosed: <code>{pct}%</code>\nAt: <code>{pips:+.1f} pips</code>")

    def notify_daily_dd_hit(self, dd_pct, max_pct):
        self.send(f"🛑 <b>DAILY DD LIMIT</b>\nCurrent: <code>{dd_pct:.2f}%</code>\nMax: <code>{max_pct:.1f}%</code>")

    def notify_kill_switch(self, reason):
        self.send(f"⚠️ <b>KILL-SWITCH</b>\n<code>{reason}</code>")

    def notify_session_block(self, session):
        pass  # silent

    def notify_news_block(self, news_time):
        pass  # silent


def create_notifier(bot_token: str, chat_id: str, enabled: bool = True):
    return TelegramNotifier(bot_token, chat_id, enabled)
