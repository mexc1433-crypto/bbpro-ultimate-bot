"""
storage/database.py
===================
SQLite database for trade logging and analytics.

Tables:
  - trades:        every opened/closed trade with P/L
  - equity_curve:  equity snapshot every poll tick
  - signals:       every entry signal (even rejected ones)
  - errors:        every exception / kill-switch event
"""

import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    open_time       TEXT NOT NULL,
    close_time      TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,           -- buy / sell
    volume_units    REAL NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL,
    sl_price        REAL,
    tp_price        REAL,
    sl_pips         REAL,
    tp_pips         REAL,
    close_reason    TEXT,                    -- tp / sl / be / trail / partial / rsi_exit / max_bars / session_end / friday
    pips_result     REAL,
    profit_amount   REAL,
    atr_at_open     REAL,
    adx_at_open     REAL,
    rsi_at_open     REAL,
    label           TEXT
);

CREATE TABLE IF NOT EXISTS equity_curve (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    equity          REAL NOT NULL,
    balance         REAL NOT NULL,
    open_positions  INTEGER NOT NULL,
    floating_pnl    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    direction       TEXT NOT NULL,           -- buy / sell
    accepted        INTEGER NOT NULL,        -- 0 or 1
    reject_reason   TEXT,
    price           REAL,
    rsi             REAL,
    ema_fast        REAL,
    ema_slow        REAL,
    atr             REAL,
    adx             REAL
);

CREATE TABLE IF NOT EXISTS errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    severity        TEXT NOT NULL,           -- warning / error / kill_switch
    category        TEXT NOT NULL,           -- order / connection / data / logic
    message         TEXT NOT NULL,
    traceback       TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_open_time ON trades(open_time);
CREATE INDEX IF NOT EXISTS idx_trades_symbol    ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_equity_ts        ON equity_curve(ts);
"""


class TradeDB:
    """Persistent trade log + analytics source."""

    def __init__(self, db_path: str = "bbpro.db"):
        self.db_path = Path(db_path)
        import os
        os.makedirs(str(self.db_path.parent), exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA)
        logger.info("Database ready: %s", self.db_path)

    # ===================================================================
    #  TRADES
    # ===================================================================
    def log_trade_open(self, *, symbol: str, side: str, volume_units: float,
                       entry_price: float, sl_price: Optional[float],
                       tp_price: Optional[float], sl_pips: Optional[float],
                       tp_pips: Optional[float], atr: float, adx: Optional[float],
                       rsi: Optional[float], label: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO trades
                   (open_time, symbol, side, volume_units, entry_price,
                    sl_price, tp_price, sl_pips, tp_pips, atr_at_open,
                    adx_at_open, rsi_at_open, label)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), symbol, side, volume_units,
                 entry_price, sl_price, tp_price, sl_pips, tp_pips,
                 atr, adx, rsi, label)
            )
            return cur.lastrowid

    def log_trade_close(self, *, trade_id: int, exit_price: float,
                        close_reason: str, pips_result: float,
                        profit_amount: float):
        with self._conn() as c:
            c.execute(
                """UPDATE trades SET
                       close_time = ?,
                       exit_price = ?,
                       close_reason = ?,
                       pips_result = ?,
                       profit_amount = ?
                   WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), exit_price,
                 close_reason, pips_result, profit_amount, trade_id)
            )

    # ===================================================================
    #  EQUITY CURVE
    # ===================================================================
    def log_equity(self, *, equity: float, balance: float,
                   open_positions: int, floating_pnl: float):
        with self._conn() as c:
            c.execute(
                """INSERT INTO equity_curve
                   (ts, equity, balance, open_positions, floating_pnl)
                   VALUES (?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), equity, balance,
                 open_positions, floating_pnl)
            )

    # ===================================================================
    #  SIGNALS
    # ===================================================================
    def log_signal(self, *, symbol: str, side: str, direction: str,
                   accepted: bool, reject_reason: str = "",
                   price: float = 0, rsi: float = 0, ema_fast: float = 0,
                   ema_slow: float = 0, atr: float = 0, adx: float = 0):
        with self._conn() as c:
            c.execute(
                """INSERT INTO signals
                   (ts, symbol, side, direction, accepted, reject_reason,
                    price, rsi, ema_fast, ema_slow, atr, adx)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), symbol, side,
                 direction, int(accepted), reject_reason, price, rsi,
                 ema_fast, ema_slow, atr, adx)
            )

    # ===================================================================
    #  ERRORS
    # ===================================================================
    def log_error(self, *, severity: str, category: str, message: str,
                  traceback_str: str = ""):
        with self._conn() as c:
            c.execute(
                """INSERT INTO errors (ts, severity, category, message, traceback)
                   VALUES (?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), severity, category,
                 message, traceback_str)
            )

    # ===================================================================
    #  QUERIES (for analytics)
    # ===================================================================
    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_equity_curve(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM equity_curve
                   WHERE ts >= datetime('now', ?)
                   ORDER BY ts ASC""",
                (f"-{since_hours} hours",)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_error_count(self, since_hours: int = 24) -> int:
        with self._conn() as c:
            row = c.execute(
                """SELECT COUNT(*) as n FROM errors
                   WHERE ts >= datetime('now', ?)""",
                (f"-{since_hours} hours",)
            ).fetchone()
            return row["n"] if row else 0
