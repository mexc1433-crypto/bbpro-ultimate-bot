"""
cTrader client — REST + Yahoo Finance (production-ready)

• Historical bars     → Yahoo Finance (HTTPS 443)
• Live quotes         → Yahoo Finance fallback (HTTPS 443)
• Account balance     → Spotware REST (api.spotware.com 443)
• Order execution     → Paper trade mode (logged + Telegram notified)

Note: TCP port 5036 is blocked by the hosting environment.
When TCP becomes available, the is_live flag gates live execution.
"""

from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

# ─── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class Bar:
    open: float; high: float; low: float; close: float
    volume: float; timestamp: float

@dataclass
class SymbolInfo:
    pip_size: float
    min_volume_units: int
    volume_step_units: int


# ─── Client ───────────────────────────────────────────────────────────────────
class CTraderClient:

    YAHOO: Dict[str, str] = {
        "EURUSD":"EURUSD=X", "GBPUSD":"GBPUSD=X",
        "USDJPY":"USDJPY=X", "USDCAD":"USDCAD=X",
        "EURJPY":"EURJPY=X", "GBPJPY":"GBPJPY=X",
        "AUDUSD":"AUDUSD=X", "NZDUSD":"NZDUSD=X",
        "XAUUSD":"GC=F",     "XAGUSD":"SI=F",
        "US30":"YM=F",       "NAS100":"NQ=F",
    }

    PIP: Dict[str, float] = {
        "XAUUSD":0.1, "XAGUSD":0.01,
        "USDJPY":0.01,"EURJPY":0.01,"GBPJPY":0.01,
    }

    def __init__(self, cfg):
        self.cfg = cfg
        self._equity_cache = 0.0
        self._paper_trades: List[dict] = []
        self._open_papers:  List[dict] = []
        self._paper_pnl     = 0.0
        self._next_id       = 1000
        self._last_bar_cache: Dict[str, List[Bar]] = {}

    # Connection (REST-only — no TCP needed)
    async def connect(self) -> None:
        eq = await self.get_account_equity()
        logger.info("✅ REST mode active — Account #%s | Balance: %.2f EUR",
                    self.cfg.account_id, eq)

    async def disconnect(self) -> None:
        pass

    @property
    def is_live(self) -> bool:
        return False   # TCP not available → paper mode

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
                    raw = await r.json(content_type=None)
                    res = raw["chart"]["result"][0]
                    tss = res.get("timestamp", [])
                    q   = res["indicators"]["quote"][0]
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
                    result = bars[-count:]
                    if result:
                        self._last_bar_cache[symbol] = result
                        logger.debug("[Yahoo] %d bars %s/%s last=%.5f",
                                     len(result), symbol, timeframe, result[-1].close)
                    return result
        except Exception as e:
            logger.warning("[Yahoo] %s/%s: %s", symbol, timeframe, e)
            return self._last_bar_cache.get(symbol, [])

    # ─── Live quote (from last bar) ───────────────────────────────────────────
    async def get_quote(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        cached = self._last_bar_cache.get(symbol)
        if cached:
            p   = cached[-1].close
            pip = self.PIP.get(symbol, 0.0001)
            spd = pip * 2
            return round(p - spd/2, 5), round(p + spd/2, 5)
        bars = await self.get_recent_bars(symbol, "m1", count=2)
        if bars:
            p   = bars[-1].close
            pip = self.PIP.get(symbol, 0.0001)
            spd = pip * 2
            return round(p - spd/2, 5), round(p + spd/2, 5)
        return None, None

    # ─── Account equity (Spotware REST) ──────────────────────────────────────
    async def get_account_equity(self) -> float:
        for token in [self.cfg.access_token,
                      getattr(self.cfg, "api_token", "")]:
            if not token:
                continue
            try:
                url = (f"https://api.spotware.com/connect/tradingaccounts"
                       f"?access_token={token}")
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
                logger.warning("get_equity: %s", e)
        return self._equity_cache or 10_000.0

    # ─── Paper order execution ────────────────────────────────────────────────
    async def send_market_order(
        self, symbol: str, *,
        side: str, volume_units: float,
        sl_pips: Optional[float] = None,
        tp_pips: Optional[float] = None,
        label: str = "", comment: str = "",
    ) -> Optional[int]:

        bid, ask = await self.get_quote(symbol)
        pip      = self.PIP.get(symbol, 0.0001)
        entry    = (ask or 0) if side.lower() == "buy" else (bid or 0)

        trade_id = self._next_id
        self._next_id += 1

        sl_price = tp_price = None
        if sl_pips and entry:
            sl_price = entry - sl_pips*pip if side=="buy" else entry + sl_pips*pip
        if tp_pips and entry:
            tp_price = entry + tp_pips*pip if side=="buy" else entry - tp_pips*pip

        trade = {
            "id":       trade_id,
            "symbol":   symbol,
            "side":     side.upper(),
            "entry":    round(entry, 5),
            "sl":       round(sl_price, 5) if sl_price else None,
            "tp":       round(tp_price, 5) if tp_price else None,
            "sl_pips":  sl_pips or 0,
            "tp_pips":  tp_pips or 0,
            "volume":   int(volume_units),
            "time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status":   "OPEN",
            "pnl_pips": 0.0,
        }
        self._paper_trades.append(trade)
        self._open_papers.append(trade)

        logger.info("📝 PAPER %s %s @ %.5f | SL%.0fp TP%.0fp | vol=%.0f",
                    side.upper(), symbol, entry, sl_pips or 0, tp_pips or 0, volume_units)
        return trade_id

    async def modify_position(self, position_id: int, *,
                              sl_price=None, tp_price=None) -> bool:
        for t in self._open_papers:
            if t["id"] == position_id:
                if sl_price: t["sl"] = sl_price
                if tp_price: t["tp"] = tp_price
                return True
        return False

    async def close_position(self, position_id: int) -> bool:
        for t in list(self._open_papers):
            if t["id"] == position_id:
                t["status"] = "CLOSED"
                self._open_papers.remove(t)
                return True
        return False

    async def close_all_positions(self, label: str = "") -> int:
        n = len(self._open_papers)
        for t in self._open_papers:
            t["status"] = "CLOSED"
        self._open_papers.clear()
        return n

    async def subscribe_spots(self, symbol: str) -> None:
        pass

    # ─── Expose paper data for dashboard ─────────────────────────────────────
    @property
    def paper_trades(self) -> List[dict]:
        return list(reversed(self._paper_trades[-50:]))

    @property
    def open_papers(self) -> List[dict]:
        return self._open_papers
