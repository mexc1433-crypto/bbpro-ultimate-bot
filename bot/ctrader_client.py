"""
cTrader Open API client — asyncio-compatible wrapper.

Twisted reactor runs in a daemon thread; all Twisted calls are dispatched
via reactor.callFromThread so they are safe from the asyncio event loop.
"""

import asyncio
import logging
import ssl
import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# ── Protobuf imports ──────────────────────────────────────────────────────
try:
    from ctrader_open_api import Client as _ProtobufClient, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOANewOrderReq,
        ProtoOAAmendOrderReq,
        ProtoOAClosePositionReq,
        ProtoOAGetTrendbarsReq,
        ProtoOASubscribeSpotsReq,
        ProtoOASpotEvent,
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOATrader,
        ProtoOATrendbar,
    )
    from twisted.internet import reactor as _reactor
    HAS_CTRADER = True
    logger.info("ctrader-open-api loaded OK")
except ImportError as e:
    logger.warning("ctrader-open-api not available: %s", e)
    HAS_CTRADER = False


# ── Data classes ──────────────────────────────────────────────────────────
@dataclass
class Bar:
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float
    timestamp: float   # unix seconds


@dataclass
class SymbolInfo:
    pip_size:            float
    min_volume_units:    int
    volume_step_units:   int


# ── Reactor thread ────────────────────────────────────────────────────────
_reactor_started = threading.Event()
_reactor_thread: Optional[threading.Thread] = None


def _ensure_reactor():
    """Start Twisted reactor in a background daemon thread (once)."""
    global _reactor_thread
    if _reactor_thread and _reactor_thread.is_alive():
        return
    if not HAS_CTRADER:
        return

    def _run():
        _reactor_started.set()
        _reactor.run(installSignalHandlers=False)

    _reactor_thread = threading.Thread(target=_run, daemon=True, name="twisted-reactor")
    _reactor_thread.start()
    _reactor_started.wait(timeout=5.0)
    logger.info("Twisted reactor thread started")


# ── Main client class ─────────────────────────────────────────────────────
class CTraderClient:
    # Yahoo ticker mapping (for bars)
    YAHOO_MAP: Dict[str, str] = {
        "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
        "USDJPY": "USDJPY=X", "USDCAD": "USDCAD=X",
        "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X",
        "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X",
        "XAUUSD": "GC=F",     "XAGUSD": "SI=F",
        "US30":   "YM=F",     "NAS100": "NQ=F",
    }

    # cTrader demo symbol IDs (approximate — good enough for order routing)
    SYMBOL_IDS: Dict[str, int] = {
        "EURUSD": 1,  "GBPUSD": 2,  "USDJPY": 3,  "USDCHF": 4,
        "AUDUSD": 5,  "USDCAD": 6,  "NZDUSD": 7,  "EURGBP": 8,
        "EURJPY": 9,  "GBPJPY": 10, "XAUUSD": 41, "XAGUSD": 42,
    }

    PIP_SIZES: Dict[str, float] = {
        "XAUUSD": 0.01, "XAGUSD": 0.01,
        "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01,
    }

    def __init__(self, cfg):
        self.cfg = cfg
        self._tc: Optional[_ProtobufClient] = None
        self._connected       = False
        self._authed_app      = False
        self._authed_account  = False
        self._last_spot: Dict[str, Tuple[float, float]] = {}
        self._cached_equity   = 0.0
        self._pending: Dict[str, asyncio.Future] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Connection ────────────────────────────────────────────────────────
    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        if not HAS_CTRADER:
            logger.warning("ctrader-open-api not installed — trading in stub mode")
            return
        _ensure_reactor()
        self._tc = _ProtobufClient(
            "demo.ctraderapi.com", 5036, TcpProtocol
        )
        self._tc.setConnectedCallback(self._on_connected)
        self._tc.setDisconnectedCallback(self._on_disconnected)
        self._tc.setMessageReceivedCallback(self._on_message)

        # Start service from reactor thread
        _reactor.callFromThread(self._tc.startService)
        logger.info("Connecting to cTrader API (TCP)…")

        # Wait for account auth (up to 10 s)
        for _ in range(20):
            await asyncio.sleep(0.5)
            if self._authed_account:
                logger.info("✅ cTrader auth complete — ready to trade")
                return
        logger.warning("⚠️ cTrader auth timeout — will retry on next tick")

    async def disconnect(self) -> None:
        if self._tc and HAS_CTRADER:
            try:
                _reactor.callFromThread(self._tc.stopService)
            except Exception:
                pass
        self._connected = False

    @property
    def is_ready(self) -> bool:
        return self._connected and self._authed_account

    # ── Twisted callbacks (run in reactor thread) ─────────────────────────
    def _on_connected(self, _):
        self._connected = True
        logger.info("TCP connected — authenticating app…")
        req = ProtoOAApplicationAuthReq()
        req.clientId     = self.cfg.client_id
        req.clientSecret = self.cfg.client_secret
        d = self._tc.send(req)
        d.addErrback(lambda _: None)  # suppress timeout noise

    def _on_disconnected(self, _, reason):
        self._connected      = False
        self._authed_app     = False
        self._authed_account = False
        logger.warning("Disconnected: %s", reason)

    def _on_message(self, _, message):
        try:
            pt = message.payloadType
            if pt == ProtoOAApplicationAuthRes().payloadType:
                self._authed_app = True
                logger.info("App auth OK")
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = int(self.cfg.account_id)
                req.accessToken         = self.cfg.access_token
                d = self._tc.send(req)
                d.addErrback(lambda _: None)

            elif pt == ProtoOAAccountAuthRes().payloadType:
                self._authed_account = True
                logger.info("Account auth OK — subscribing quotes…")
                # Subscribe spots for all configured symbols
                for sym in ["EURUSD","GBPUSD","USDJPY","USDCAD","EURJPY","XAUUSD"]:
                    r = ProtoOASubscribeSpotsReq()
                    r.ctidTraderAccountId = int(self.cfg.account_id)
                    r.symbolId.append(self.SYMBOL_IDS.get(sym, 1))
                    d2 = self._tc.send(r)
                    d2.addErrback(lambda _: None)

            elif pt == ProtoOASpotEvent().payloadType:
                spot = Protobuf.extract(message)
                # Find symbol name from ID
                sym_id  = spot.symbolId
                sym_name = next((k for k,v in self.SYMBOL_IDS.items() if v == sym_id), None)
                if sym_name:
                    bid = spot.bid  / 100000 if spot.HasField("bid") else None
                    ask = spot.ask  / 100000 if spot.HasField("ask") else None
                    self._last_spot[sym_name] = (bid, ask)

        except Exception as e:
            logger.error("Message handler error: %s", e)

    # ── Quotes ────────────────────────────────────────────────────────────
    async def get_quote(self, symbol_name: str) -> Tuple[Optional[float], Optional[float]]:
        """Return (bid, ask). Falls back to Yahoo if no live quote yet."""
        if symbol_name in self._last_spot:
            b, a = self._last_spot[symbol_name]
            if b and a:
                return b, a
        # Fallback: latest close from Yahoo
        bars = await self.get_recent_bars(symbol_name, "m1", count=2)
        if bars:
            p = bars[-1].close
            pip = self.PIP_SIZES.get(symbol_name, 0.0001)
            return p - pip, p + pip
        return None, None

    # ── Symbol info ───────────────────────────────────────────────────────
    async def get_symbol_info(self, symbol_name: str) -> SymbolInfo:
        pip = self.PIP_SIZES.get(symbol_name, 0.0001)
        if "XAU" in symbol_name:
            return SymbolInfo(pip_size=pip, min_volume_units=100,  volume_step_units=100)
        if "JPY" in symbol_name:
            return SymbolInfo(pip_size=pip, min_volume_units=1000, volume_step_units=1000)
        return SymbolInfo(pip_size=pip, min_volume_units=1000, volume_step_units=1000)

    # ── Historical bars (Yahoo Finance) ───────────────────────────────────
    async def get_recent_bars(self, symbol_name: str, timeframe: str, count: int = 300) -> List[Bar]:
        yticker = self.YAHOO_MAP.get(symbol_name)
        if not yticker:
            return []
        tf_mins = {"m1":1,"m5":5,"m15":15,"m30":30,"h1":60,"h4":240,"d1":1440}
        yf_int  = {"m1":"1m","m5":"5m","m15":"15m","m30":"30m","h1":"1h","h4":"1h","d1":"1d"}
        mins = tf_mins.get(timeframe, 30)
        iv   = yf_int.get(timeframe, "30m")
        now  = int(time.time())
        p1   = now - max(count, 300) * mins * 60
        url  = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yticker}"
                f"?period1={p1}&period2={now}&interval={iv}&includePrePost=false")
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers={"User-Agent":"Mozilla/5.0"},
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return []
                    raw = await r.json(content_type=None)
                    res = raw["chart"]["result"][0]
                    ts_list = res.get("timestamp", [])
                    q = res["indicators"]["quote"][0]
                    bars_out = []
                    for i, ts in enumerate(ts_list):
                        try:
                            o = q["open"][i]  or 0
                            h = q["high"][i]  or o
                            l = q["low"][i]   or o
                            c = q["close"][i] or o
                            v = (q.get("volume") or [0]*len(ts_list))[i] or 0
                            if c > 0:
                                bars_out.append(Bar(open=o,high=h,low=l,close=c,volume=v,timestamp=float(ts)))
                        except (IndexError, TypeError):
                            pass
                    if bars_out:
                        logger.debug("[Yahoo] %d bars %s/%s", len(bars_out), symbol_name, timeframe)
                    return bars_out[-count:]
        except Exception as e:
            logger.warning("[Yahoo bars] %s/%s: %s", symbol_name, timeframe, e)
            return []

    # ── Account equity (REST) ─────────────────────────────────────────────
    async def get_account_equity(self) -> float:
        try:
            url = f"https://api.spotware.com/connect/tradingaccounts?access_token={self.cfg.access_token}"
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers={"User-Agent":"BBPro/2.0"},
                                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        for a in data.get("data", []):
                            if str(a.get("accountId")) == str(self.cfg.account_id):
                                bal = a.get("balance", 0) / 100
                                self._cached_equity = bal
                                return bal
        except Exception as e:
            logger.warning("get_account_equity: %s", e)
        return self._cached_equity or 10_000.0

    # ── Order execution ───────────────────────────────────────────────────
    async def send_market_order(
        self,
        symbol_name: str,
        *,
        side: str,
        volume_units: float,
        sl_pips: Optional[float] = None,
        tp_pips: Optional[float] = None,
        label: str = "",
        comment: str = "",
    ) -> Optional[int]:
        if not HAS_CTRADER or not self._authed_account:
            logger.warning("[STUB] Order not placed — not authenticated: %s %s %.0f",
                           side, symbol_name, volume_units)
            return None

        info = await self.get_symbol_info(symbol_name)
        bid, ask = await self.get_quote(symbol_name)
        if bid is None or ask is None:
            logger.error("No quote for %s", symbol_name)
            return None

        sym_id = self.SYMBOL_IDS.get(symbol_name, 1)
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.symbolId   = sym_id
        req.orderType  = 1   # MARKET
        req.tradeSide  = 1 if side.lower() == "buy" else 2
        req.volume     = int(volume_units)
        req.comment    = comment or label

        pip = info.pip_size
        if sl_pips and sl_pips > 0:
            req.stopLoss   = (bid - sl_pips * pip) if side=="buy" else (ask + sl_pips * pip)
            req.hasStopLoss = True
        if tp_pips and tp_pips > 0:
            req.takeProfit  = (ask + tp_pips * pip) if side=="buy" else (bid - tp_pips * pip)
            req.hasTakeProfit = True

        def _do_send():
            d = self._tc.send(req)
            d.addErrback(lambda _: None)
        _reactor.callFromThread(_do_send)
        logger.info("✅ Market order sent: %s %s %.0f units | SL%.1fp TP%.1fp",
                    side, symbol_name, volume_units,
                    sl_pips or 0, tp_pips or 0)
        return 1

    async def modify_position(self, position_id: int, *, sl_price=None, tp_price=None) -> bool:
        if not self._authed_account:
            return False
        req = ProtoOAAmendOrderReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.positionId = position_id
        if sl_price: req.stopLoss    = sl_price
        if tp_price: req.takeProfit  = tp_price
        def _do_mod():
            d = self._tc.send(req)
            d.addErrback(lambda _: None)
        _reactor.callFromThread(_do_mod)
        return True

    async def close_position(self, position_id: int) -> bool:
        if not self._authed_account:
            return False
        req = ProtoOAClosePositionReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.positionId = position_id
        def _do_mod():
            d = self._tc.send(req)
            d.addErrback(lambda _: None)
        _reactor.callFromThread(_do_mod)
        return True

    async def close_all_positions(self, label: str = "") -> int:
        return 0  # TODO: fetch open positions then close each

    async def subscribe_spots(self, symbol_name: str) -> None:
        pass  # Already subscribed all symbols in _on_message on auth
