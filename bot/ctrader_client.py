"""
cTrader client — hybrid REST + Twisted TCP bridge.

• Historical bars  → Yahoo Finance (HTTPS/443, always works)
• Live quotes      → Twisted TCP to demo.ctraderapi.com:5036 (Railway can reach it)
                     Falls back to Yahoo Finance last close if TCP unavailable
• Order execution  → TCP if authenticated, else paper-trade mode with full logging
• Account equity   → Spotware REST (api.spotware.com/443, always works)
"""

from __future__ import annotations
import asyncio
import logging
import ssl
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Protobuf / Twisted — optional (graceful stub if unavailable or port blocked)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from ctrader_open_api import Client as _TcpClient, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,  ProtoOAApplicationAuthRes,
        ProtoOAAccountAuthReq,      ProtoOAAccountAuthRes,
        ProtoOANewOrderReq,
        ProtoOAAmendPositionSLTPReq,
        ProtoOAClosePositionReq,
        ProtoOASubscribeSpotsReq,   ProtoOASpotEvent,
    )
    from twisted.internet import reactor as _reactor
    _HAS_PROTO = True
except ImportError:
    _HAS_PROTO = False
    logger.warning("ctrader-open-api not installed — running in REST-only mode")


# ─────────────────────────────────────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Bar:
    open: float; high: float; low: float; close: float
    volume: float; timestamp: float          # unix seconds

@dataclass
class SymbolInfo:
    pip_size:         float
    min_volume_units: int
    volume_step_units: int


# ─────────────────────────────────────────────────────────────────────────────
#  Twisted reactor thread (singleton)
# ─────────────────────────────────────────────────────────────────────────────
_reactor_thread: Optional[threading.Thread] = None
_reactor_ready  = threading.Event()

def _start_reactor():
    global _reactor_thread
    if not _HAS_PROTO:
        return
    if _reactor_thread and _reactor_thread.is_alive():
        return
    def _run():
        _reactor_ready.set()
        _reactor.run(installSignalHandlers=False)
    _reactor_thread = threading.Thread(target=_run, daemon=True, name="twisted")
    _reactor_thread.start()
    _reactor_ready.wait(timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
#  Main client
# ─────────────────────────────────────────────────────────────────────────────
class CTraderClient:

    # Yahoo Finance ticker map
    YAHOO: Dict[str, str] = {
        "EURUSD":"EURUSD=X", "GBPUSD":"GBPUSD=X",
        "USDJPY":"USDJPY=X", "USDCAD":"USDCAD=X",
        "EURJPY":"EURJPY=X", "GBPJPY":"GBPJPY=X",
        "AUDUSD":"AUDUSD=X", "NZDUSD":"NZDUSD=X",
        "XAUUSD":"GC=F",     "XAGUSD":"SI=F",
        "US30":"YM=F",       "NAS100":"NQ=F",
    }

    # cTrader demo symbol IDs
    SYM_ID: Dict[str, int] = {
        "EURUSD":1,"GBPUSD":2,"USDJPY":3,"USDCHF":4,
        "AUDUSD":5,"USDCAD":6,"NZDUSD":7,"EURGBP":8,
        "EURJPY":9,"GBPJPY":10,"XAUUSD":41,"XAGUSD":42,
    }

    PIP: Dict[str, float] = {
        "XAUUSD":0.1,"XAGUSD":0.01,
        "USDJPY":0.01,"EURJPY":0.01,"GBPJPY":0.01,
    }

    def __init__(self, cfg):
        self.cfg = cfg
        self._tc: Optional[_TcpClient] = None
        self._connected       = False
        self._authed_app      = False
        self._authed_account  = False
        self._spot: Dict[str, Tuple[float, float]] = {}
        self._equity_cache    = 0.0
        self._paper_trades: List[dict] = []
        self._paper_pnl       = 0.0

    # ─── Connection ──────────────────────────────────────────────────────────
    async def connect(self) -> None:
        if not _HAS_PROTO:
            logger.warning("REST-only mode (ctrader-open-api missing)")
            return
        _start_reactor()
        self._tc = _TcpClient("demo.ctraderapi.com", 5036, TcpProtocol)
        self._tc.setConnectedCallback(self._cb_connected)
        self._tc.setDisconnectedCallback(self._cb_disconnected)
        self._tc.setMessageReceivedCallback(self._cb_message)
        _reactor.callFromThread(self._tc.startService)
        logger.info("Connecting to cTrader TCP (demo.ctraderapi.com:5036)…")
        # wait up to 12 s for auth
        for _ in range(24):
            await asyncio.sleep(0.5)
            if self._authed_account:
                logger.info("✅ cTrader TCP auth complete — live order mode")
                return
        logger.warning("⚠️ TCP auth timeout — paper-trade mode active")

    async def disconnect(self):
        if self._tc and _HAS_PROTO:
            try:
                _reactor.callFromThread(self._tc.stopService)
            except Exception:
                pass

    @property
    def is_live(self) -> bool:
        """True when TCP is authenticated and orders can be placed on cTrader."""
        return self._authed_account

    # ─── Twisted callbacks (reactor thread) ──────────────────────────────────
    def _cb_connected(self, _):
        self._connected = True
        logger.info("TCP connected — sending app auth")
        req = ProtoOAApplicationAuthReq()
        req.clientId     = self.cfg.client_id
        req.clientSecret = self.cfg.client_secret
        d = self._tc.send(req)
        d.addErrback(lambda f: logger.debug("AppAuth deferred: %s", f))

    def _cb_disconnected(self, _, reason):
        was = self._authed_account
        self._connected = self._authed_app = self._authed_account = False
        if was:
            logger.warning("TCP disconnected: %s", reason)

    def _cb_message(self, _, msg):
        try:
            pt = msg.payloadType
            if pt == ProtoOAApplicationAuthRes().payloadType:
                self._authed_app = True
                logger.info("App auth OK — sending account auth")
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = int(self.cfg.account_id)
                req.accessToken = self.cfg.access_token
                d = self._tc.send(req)
                d.addErrback(lambda f: logger.debug("AcctAuth deferred: %s", f))

            elif pt == ProtoOAAccountAuthRes().payloadType:
                self._authed_account = True
                logger.info("✅ Account auth OK — subscribing spots")
                for sym, sid in self.SYM_ID.items():
                    r = ProtoOASubscribeSpotsReq()
                    r.ctidTraderAccountId = int(self.cfg.account_id)
                    r.symbolId.append(sid)
                    d = self._tc.send(r)
                    d.addErrback(lambda f: None)

            elif pt == ProtoOASpotEvent().payloadType:
                spot = Protobuf.extract(msg)
                sym_name = next((k for k, v in self.SYM_ID.items() if v == spot.symbolId), None)
                if sym_name and spot.HasField("bid") and spot.HasField("ask"):
                    self._spot[sym_name] = (spot.bid / 100000, spot.ask / 100000)

        except Exception as e:
            logger.error("TCP message error: %s", e)

    # ─── Quotes ──────────────────────────────────────────────────────────────
    async def get_quote(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        # Live TCP quote
        if symbol in self._spot:
            b, a = self._spot[symbol]
            if b and a:
                return b, a
        # Fallback: derive from last Yahoo bar
        bars = await self.get_recent_bars(symbol, "m1", count=2)
        if bars:
            p   = bars[-1].close
            pip = self.PIP.get(symbol, 0.0001)
            spread = pip * 2
            return round(p - spread/2, 5), round(p + spread/2, 5)
        return None, None

    # ─── Symbol info ─────────────────────────────────────────────────────────
    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        pip = self.PIP.get(symbol, 0.0001)
        if "XAU" in symbol:
            return SymbolInfo(pip, 100, 100)
        return SymbolInfo(pip, 1000, 1000)

    # ─── Historical bars (Yahoo Finance) ─────────────────────────────────────
    async def get_recent_bars(self, symbol: str, timeframe: str,
                               count: int = 300) -> List[Bar]:
        ticker = self.YAHOO.get(symbol)
        if not ticker:
            return []
        TF_MINS = {"m1":1,"m5":5,"m15":15,"m30":30,"h1":60,"h4":240,"d1":1440}
        YF_IV   = {"m1":"1m","m5":"5m","m15":"15m","m30":"30m",
                   "h1":"1h","h4":"1h","d1":"1d"}
        mins = TF_MINS.get(timeframe, 30)
        iv   = YF_IV.get(timeframe, "30m")
        now  = int(time.time())
        p1   = now - max(count, 300) * mins * 60
        url  = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                f"?period1={p1}&period2={now}&interval={iv}&includePrePost=false")
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url,
                                    headers={"User-Agent":"Mozilla/5.0"},
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return []
                    raw  = await r.json(content_type=None)
                    res  = raw["chart"]["result"][0]
                    tss  = res.get("timestamp", [])
                    q    = res["indicators"]["quote"][0]
                    bars = []
                    for i, ts in enumerate(tss):
                        try:
                            o = q["open"][i]  or 0
                            h = q["high"][i]  or o
                            l = q["low"][i]   or o
                            c = q["close"][i] or o
                            v = (q.get("volume") or [0]*len(tss))[i] or 0
                            if c > 0:
                                bars.append(Bar(o, h, l, c, v, float(ts)))
                        except (IndexError, TypeError):
                            pass
                    return bars[-count:]
        except Exception as e:
            logger.warning("[Yahoo] %s/%s: %s", symbol, timeframe, e)
            return []

    # ─── Account equity (Spotware REST) ──────────────────────────────────────
    async def get_account_equity(self) -> float:
        try:
            url = (f"https://api.spotware.com/connect/tradingaccounts"
                   f"?access_token={self.cfg.access_token}")
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url,
                                    headers={"User-Agent":"BBPro/2.0"},
                                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        for a in data.get("data", []):
                            if str(a.get("accountId")) == str(self.cfg.account_id):
                                bal = a["balance"] / 100
                                self._equity_cache = bal
                                return bal
        except Exception as e:
            logger.warning("get_equity REST: %s", e)
        return self._equity_cache or 10_000.0

    # ─── Order execution ─────────────────────────────────────────────────────
    async def send_market_order(
        self, symbol: str, *,
        side: str, volume_units: float,
        sl_pips: Optional[float] = None,
        tp_pips: Optional[float] = None,
        label: str = "", comment: str = "",
    ) -> Optional[int]:

        info          = await self.get_symbol_info(symbol)
        bid, ask      = await self.get_quote(symbol)
        pip           = info.pip_size

        # ── LIVE TCP path ────────────────────────────────────────────────────
        if self.is_live and _HAS_PROTO:
            if bid is None or ask is None:
                logger.error("No quote for %s — skipping order", symbol)
                return None
            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = int(self.cfg.account_id)
            req.symbolId  = self.SYM_ID.get(symbol, 1)
            req.orderType = 1   # MARKET
            req.tradeSide = 1 if side.lower() == "buy" else 2
            req.volume    = int(volume_units)
            req.comment   = comment or label
            entry = ask if side.lower() == "buy" else bid
            if sl_pips and sl_pips > 0:
                req.relativeStopLoss  = int(sl_pips * 10)   # in 1/10 pip
            if tp_pips and tp_pips > 0:
                req.relativeTakeProfit = int(tp_pips * 10)
            def _do():
                d = self._tc.send(req)
                d.addErrback(lambda f: logger.warning("Order deferred: %s", f))
            _reactor.callFromThread(_do)
            logger.info("🟢 LIVE order → %s %s %.0f units SL%.0fp TP%.0fp",
                        side.upper(), symbol, volume_units, sl_pips or 0, tp_pips or 0)
            return 1

        # ── Paper-trade path ─────────────────────────────────────────────────
        entry_price = (ask or bid or 0) if side.lower() == "buy" else (bid or ask or 0)
        trade = {
            "id":        int(time.time() * 1000),
            "symbol":    symbol,
            "side":      side.upper(),
            "entry":     entry_price,
            "volume":    int(volume_units),
            "sl_pips":   sl_pips or 0,
            "tp_pips":   tp_pips or 0,
            "time":      time.strftime("%H:%M:%S UTC"),
            "status":    "PAPER",
        }
        self._paper_trades.append(trade)
        logger.info("📝 PAPER order → %s %s @ %.5f SL%.0fp TP%.0fp",
                    side.upper(), symbol, entry_price, sl_pips or 0, tp_pips or 0)
        return trade["id"]

    async def modify_position(self, position_id: int, *,
                              sl_price: Optional[float] = None,
                              tp_price: Optional[float] = None) -> bool:
        if not self.is_live or not _HAS_PROTO:
            return False
        req = ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.positionId = position_id
        if sl_price: req.stopLoss   = sl_price
        if tp_price: req.takeProfit = tp_price
        def _do():
            d = self._tc.send(req)
            d.addErrback(lambda f: None)
        _reactor.callFromThread(_do)
        return True

    async def close_position(self, position_id: int) -> bool:
        if not self.is_live or not _HAS_PROTO:
            return False
        req = ProtoOAClosePositionReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.positionId = position_id
        def _do():
            d = self._tc.send(req)
            d.addErrback(lambda f: None)
        _reactor.callFromThread(_do)
        return True

    async def close_all_positions(self, label: str = "") -> int:
        return 0

    async def subscribe_spots(self, symbol: str) -> None:
        pass  # handled automatically in _cb_message on account auth

    # ─── Paper trade info ─────────────────────────────────────────────────────
    @property
    def paper_trades(self) -> List[dict]:
        return self._paper_trades

    @property
    def paper_pnl(self) -> float:
        return self._paper_pnl
