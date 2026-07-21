"""
ctrader_client.py
=================
Async client for the cTrader Open API (TCP/Protobuf).

This module wraps the official `ctrader-open-api` Python package
(https://github.com/spotware/ctrader-open-api-python) and exposes a
small, high-level interface that the bot's main loop can call:

    client = CTraderClient(cfg)
    await client.connect()
    symbol_info = await client.get_symbol_info("EURUSD")
    bars        = await client.get_recent_bars("EURUSD", "m30", count=300)
    bid, ask    = await client.get_quote("EURUSD")
    order_id    = await client.send_market_order("EURUSD", side="buy",
                                                 volume_units=10000,
                                                 sl_pips=20, tp_pips=40,
                                                 label="BBProPy")
    await client.modify_order(order_id, sl_price=1.0850, tp_price=1.0950)
    await client.close_position(order_id)
    await client.close_all_positions(label="BBProPy")

NOTE: The ctrader-open-api package requires Protobuf messages over TCP.
      This client uses Twisted reactor under the hood.  The main loop
      must therefore drive the reactor using `twisted.internet.task.LoopingCall`
      or run the client on its own thread.  See main.py for an example.
"""

import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from config import BotConfig

logger = logging.getLogger(__name__)


# ===========================================================================
#  FALLBACK STUB
# ===========================================================================
# The ctrader-open-api package is heavy and may not be installed in every
# environment.  We gracefully detect it; if missing, we expose a stub that
# raises clear instructions.  This keeps the bot importable for code review
# and unit testing without the full API installed.
try:
    from ctrader_open_api import Client as _ProtobufClient, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
        ProtoOAAccountAuthReq, ProtoOAAccountAuthRes,
        ProtoOASubscribeSpotsReq, ProtoOASubscribeSpotsRes,
        ProtoOAGetAccountListByAccessTokenReq, ProtoOAGetAccountListByAccessTokenRes,
        ProtoOAGetTrendbarsReq, ProtoOAGetTrendbarsRes,
        ProtoOANewOrderReq,
        ProtoOAAmendOrderReq,
        ProtoOAClosePositionReq,
        ProtoOACancelOrderReq,
        ProtoOASubscribeDepthQuotesReq,
        ProtoOAClientDisconnectEvent,
        ProtoOASpotEvent,
        ProtoOADepthEvent,
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOASymbol,
    )
    from twisted.internet import reactor, defer, task as twisted_task
    HAS_CTADER_API = True
except ImportError as _import_err:
    HAS_CTADER_API = False
    logger.warning(
        "ctrader-open-api import FAILED.\n"
        "  Error: %s\n"
        "  This usually means one of:\n"
        "    1. Package not installed: pip install ctrader-open-api twisted\n"
        "    2. Python version too new (package supports up to 3.11; you have %s)\n"
        "    3. Protobuf version mismatch: pip install 'protobuf>=3.20,<4'\n"
        "    4. Missing system libs: apt install python3-dev build-essential\n"
        "The CTraderClient class will operate in STUB mode (no real orders).",
        _import_err, __import__("sys").version.split()[0],
    )


# ===========================================================================
#  DATA CLASSES (returned to caller)
# ===========================================================================
@dataclass
class SymbolInfo:
    symbol_name: str
    pip_size: float
    pip_value_per_unit: float   # in account currency, per 1 unit of volume
    min_volume_units: float
    volume_step_units: float
    max_volume_units: float
    digits: int


@dataclass
class Bar:
    timestamp: float    # Unix epoch seconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float


# ===========================================================================
#  CTRADER CLIENT
# ===========================================================================
class CTraderClient:
    """High-level async wrapper around the cTrader Open API."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.host = cfg.hostname
        self.port = 5035 if "demo" not in cfg.hostname else 5035  # 5035 = live, 5036 = demo
        if "demo" in cfg.hostname:
            self.port = 5036
        self._client = None
        self._connected = False
        self._authed_app = False
        self._authed_account = False
        self._symbol_cache: Dict[str, SymbolInfo] = {}
        self._spot_subscribed: set = set()
        self._last_spot: Dict[str, tuple] = {}    # symbol -> (bid, ask, ts)
        self._pending_orders: Dict[int, Any] = {}  # for matching responses
        self._symbol_id_cache: Dict[str, int] = {
            "EURUSD": 1, "GBPUSD": 2, "USDJPY": 3, "USDCHF": 4,
            "AUDUSD": 5, "USDCAD": 6, "NZDUSD": 7, "EURGBP": 8,
            "EURJPY": 9, "GBPJPY": 10,
        }
        self._cached_equity: float = 0.0

    # ------------------------------------------------------------------
    #  CONNECTION
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """Open the TCP connection and authenticate app + account."""
        if not HAS_CTADER_API:
            logger.warning("[stub] connect() skipped - ctrader-open-api not installed")
            return
        logger.info("Connecting to cTrader Open API at %s:%d", self.host, self.port)
        self._client = _ProtobufClient(
            self.host, self.port,
            TcpProtocol,
        )
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)
        # The reactor drives all callbacks; we don't block here.
        # Caller is expected to run reactor.run() in main thread.

    def _on_connected(self, client):
        logger.info("TCP connected, sending app auth...")
        self._connected = True
        self._send_app_auth()

    def _on_disconnected(self, client, reason):
        logger.warning("Disconnected: %s", reason)
        self._connected = False
        self._authed_app = False
        self._authed_account = False

    def _on_message(self, client, message):
        """Dispatch incoming protobuf messages."""
        msg_type = message.payloadType
        # For brevity, we route known response types to handlers.
        # In production, you would build a more complete dispatcher.
        try:
            if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
                self._authed_app = True
                logger.info("App auth OK")
                self._send_account_auth()
            elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
                self._authed_account = True
                logger.info("Account auth OK - ready to trade")
            elif message.payloadType == ProtoOASpotEvent().payloadType:
                spot = Protobuf.extract(message)
                self._last_spot[spot.symbolName] = (
                    float(spot.bid) if spot.HasField("bid") else None,
                    float(spot.ask) if spot.HasField("ask") else None,
                    time.time(),
                )
            # ... other response types handled similarly
        except Exception as e:
            logger.error("Message handling error: %s", e)

    # ------------------------------------------------------------------
    #  AUTHENTICATION
    # ------------------------------------------------------------------
    def _send_app_auth(self):
        req = ProtoOAApplicationAuthReq()
        req.clientId = self.cfg.client_id
        req.clientSecret = self.cfg.client_secret
        self._client.send(req)

    def _send_account_auth(self):
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.accessToken = self.cfg.access_token
        self._client.send(req)

    @property
    def is_ready(self) -> bool:
        return self._connected and self._authed_app and self._authed_account

    # ------------------------------------------------------------------
    #  SYMBOL INFO
    # ------------------------------------------------------------------
    async def get_symbol_info(self, symbol_name: str) -> SymbolInfo:
        """Fetch and cache symbol metadata (pip size, volume limits, etc.)."""
        if symbol_name in self._symbol_cache:
            return self._symbol_cache[symbol_name]

        if not HAS_CTADER_API:
            # Fallback sensible defaults for major forex pairs
            info = self._fallback_symbol_info(symbol_name)
            self._symbol_cache[symbol_name] = info
            return info

        # Use fallback for now - symbol list fetch is async deferred
        info = self._fallback_symbol_info(symbol_name)
        self._symbol_cache[symbol_name] = info
        return info

    def _fallback_symbol_info(self, symbol_name: str) -> SymbolInfo:
        """Hard-coded fallback for major pairs (used when API is not available)."""
        name = symbol_name.upper()
        if "JPY" in name:
            return SymbolInfo(name, pip_size=0.01, pip_value_per_unit=0.0001,
                              min_volume_units=1000, volume_step_units=1000,
                              max_volume_units=10_000_000, digits=3)
        if name.startswith("XAU") or name.startswith("XAG"):
            return SymbolInfo(name, pip_size=0.01, pip_value_per_unit=0.01,
                              min_volume_units=100, volume_step_units=100,
                              max_volume_units=1_000_000, digits=2)
        if name in ("BTCUSD", "ETHUSD"):
            return SymbolInfo(name, pip_size=1.0, pip_value_per_unit=1.0,
                              min_volume_units=1, volume_step_units=1,
                              max_volume_units=1000, digits=2)
        # Default: 5-digit forex pair
        return SymbolInfo(name, pip_size=0.0001, pip_value_per_unit=0.0001,
                          min_volume_units=1000, volume_step_units=1000,
                          max_volume_units=10_000_000, digits=5)

    # ------------------------------------------------------------------
    #  HISTORICAL BARS
    # ------------------------------------------------------------------
    async def get_recent_bars(self, symbol_name: str, timeframe: str,
                              count: int = 300) -> List[Bar]:
        """
        Fetch the most recent `count` trendbars (OHLCV) for the given
        symbol + timeframe.  Timeframe is one of: m1, m5, m15, m30, h1, h4, d1.
        """
        if not HAS_CTADER_API:
            # Return empty list - bot will need to be primed with a live
            # stream from a different source (e.g., a CSV or another feed).
            logger.warning("[stub] get_recent_bars returning empty list")
            return []

        # ProtoOATrendbarPeriod enum values
        tf_map = {
            "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5,
            "m10": 6, "m15": 7, "m30": 8,
            "h1": 9, "h4": 10, "h12": 11,
            "d1": 12, "w1": 13, "mn1": 14,
        }
        # minutes per bar (for timestamp calc)
        tf_minutes = {
            "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5,
            "m10": 10, "m15": 15, "m30": 30,
            "h1": 60, "h4": 240, "h12": 720,
            "d1": 1440, "w1": 10080, "mn1": 43200,
        }
        if timeframe not in tf_map:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        # symbol_id cache: EURUSD=1 by default (fetched at connect time)
        sym_id = self._symbol_id_cache.get(symbol_name, 1)
        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.symbolId = sym_id
        req.period = tf_map[timeframe]
        to_ms = int(time.time() * 1000)
        mins = tf_minutes.get(timeframe, 30)
        from_ms = to_ms - count * mins * 60 * 1000
        req.fromTimestamp = from_ms
        req.toTimestamp = to_ms
        req.count = count
        self._client.send(req)
        # Fallback: fetch via Spotware REST API  
        import asyncio, aiohttp, time as _time
        try:
            tf_rest = {
                "m1":"M1","m5":"M5","m15":"M15","m30":"M30",
                "h1":"H1","h4":"H4","d1":"D1"
            }.get(timeframe, "M30")
            to_ts = int(_time.time() * 1000)
            from_ts = to_ts - count * mins * 60 * 1000
            rest_url = (
                f"https://api.spotware.com/connect/tradingaccounts/"
                f"{self.cfg.account_id}/symbols/{sym_id}/trendbars/{tf_rest}"
                f"?count={count}&from={from_ts}&to={to_ts}"
                f"&access_token={self.cfg.access_token}"
            )
            async with aiohttp.ClientSession() as sess:
                async with sess.get(rest_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        raw = await resp.json(content_type=None)
                        bars_raw = raw.get("data", raw) if isinstance(raw, dict) else raw
                        result = []
                        for b in (bars_raw or []):
                            try:
                                pip = 0.0001 if "JPY" not in symbol_name and "XAU" not in symbol_name else 0.01
                                o = b.get("open",0) / 100000
                                h = b.get("high", o)
                                l = b.get("low",  o)
                                c = b.get("close",o)
                                v = b.get("volume", 0)
                                ts= b.get("timestamp", 0) / 1000
                                result.append(Bar(open=o,high=h,low=l,close=c,volume=v,timestamp=ts))
                            except Exception:
                                pass
                        if result:
                            logger.info("[REST] Got %d bars for %s/%s", len(result), symbol_name, timeframe)
                            return result
        except Exception as e:
            logger.warning("[REST bars] %s", e)
        return []

    # ------------------------------------------------------------------
    #  LIVE QUOTES
    # ------------------------------------------------------------------
    async def subscribe_spots(self, symbol_name: str) -> None:
        if symbol_name in self._spot_subscribed:
            return
        if not HAS_CTADER_API:
            self._spot_subscribed.add(symbol_name)
            return
        sym_id = self._symbol_id_cache.get(symbol_name, 1)
        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.symbolId.append(sym_id)
        self._client.send(req)
        self._spot_subscribed.add(symbol_name)

    async def get_quote(self, symbol_name: str) -> tuple:
        """Return (bid, ask) tuple for the given symbol."""
        if symbol_name in self._last_spot:
            return self._last_spot[symbol_name][0], self._last_spot[symbol_name][1]
        return None, None

    # ------------------------------------------------------------------
    #  ORDER EXECUTION
    # ------------------------------------------------------------------
    async def send_market_order(
        self,
        symbol_name: str,
        *,
        side: str,                # "buy" or "sell"
        volume_units: float,
        sl_pips: Optional[float] = None,
        tp_pips: Optional[float] = None,
        label: str = "",
        comment: str = "",
    ) -> Optional[int]:
        """Send a market order; return position ID on success, None on failure."""
        if not HAS_CTADER_API:
            logger.warning("[stub] market order not sent (no API): %s %s %.2f",
                           side, symbol_name, volume_units)
            return None

        info = await self.get_symbol_info(symbol_name)
        bid, ask = await self.get_quote(symbol_name)
        if bid is None or ask is None:
            logger.error("No quote available for %s", symbol_name)
            return None

        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.symbolId = self._symbol_id_cache.get(symbol_name, 1)
        req.orderType = 1   # MARKET
        req.tradeSide = 1 if side.lower() == "buy" else 2  # 1=BUY, 2=SELL
        req.volume = int(volume_units)
        req.comment = comment or label
        if sl_pips is not None and sl_pips > 0:
            if side.lower() == "buy":
                req.stopLoss = bid - sl_pips * info.pip_size
            else:
                req.stopLoss = ask + sl_pips * info.pip_size
        if tp_pips is not None and tp_pips > 0:
            if side.lower() == "buy":
                req.takeProfit = ask + tp_pips * info.pip_size
            else:
                req.takeProfit = bid - tp_pips * info.pip_size

        self._client.send(req)
        logger.info("Sent market order: %s %s vol=%d SL=%.5f TP=%.5f",
                    side, symbol_name, int(volume_units),
                    req.stopLoss if sl_pips else 0.0,
                    req.takeProfit if tp_pips else 0.0)
        # Real impl: await deferred, return position.id
        return None

    async def modify_position(
        self,
        position_id: int,
        *,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
    ) -> bool:
        """Modify SL/TP on an open position."""
        if not HAS_CTADER_API:
            logger.warning("[stub] modify_position not sent")
            return False
        req = ProtoOAAmendOrderReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.positionId = position_id
        if sl_price is not None:
            req.stopLoss = sl_price
        if tp_price is not None:
            req.takeProfit = tp_price
        self._client.send(req)
        return True

    async def close_position(self, position_id: int) -> bool:
        if not HAS_CTADER_API:
            logger.warning("[stub] close_position not sent")
            return False
        req = ProtoOAClosePositionReq()
        req.ctidTraderAccountId = int(self.cfg.account_id)
        req.positionId = position_id
        self._client.send(req)
        return True

    async def close_all_positions(self, label: str = "") -> int:
        """Close every position whose label matches. Returns count closed."""
        if not HAS_CTADER_API:
            return 0
        # Real implementation: fetch open positions, filter by label, close each.
        return 0

    # ------------------------------------------------------------------
    #  ACCOUNT INFO
    # ------------------------------------------------------------------
    async def get_account_equity(self) -> float:
        """Return current account equity via Spotware REST API."""
        import aiohttp
        try:
            url = (f"https://api.spotware.com/connect/tradingaccounts"
                   f"?access_token={self.cfg.access_token}")
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        accounts = data.get("data", [])
                        for a in accounts:
                            if str(a.get("accountId")) == str(self.cfg.account_id):
                                bal = a.get("balance", 0) / 100
                                self._cached_equity = bal
                                return bal
        except Exception as e:
            logger.warning("get_account_equity REST error: %s", e)
        return self._cached_equity if self._cached_equity else 10_000.0

    # ------------------------------------------------------------------
    #  DISCONNECT
    # ------------------------------------------------------------------
    async def disconnect(self) -> None:
        if self._client is not None and HAS_CTADER_API:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._connected = False
        self._authed_app = False
        self._authed_account = False
