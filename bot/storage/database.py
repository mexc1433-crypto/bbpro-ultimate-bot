"""
storage/database.py - BBPro Ultimate v2
Handles all trade/equity/error persistence in SQLite.
"""
import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


def init_db(db_path: str) -> bool:
    """Initialize the database and create tables if they don't exist."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT,
                side          TEXT,
                open_time     TEXT,
                close_time    TEXT,
                volume        REAL,
                open_price    REAL,
                close_price   REAL,
                pips          REAL,
                pnl           REAL,
                close_reason  TEXT,
                sl_pips       REAL,
                tp_pips       REAL,
                atr           REAL,
                adx           REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_curve (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT DEFAULT (datetime('now')),
                equity          REAL,
                balance         REAL,
                open_positions  INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS errors (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT DEFAULT (datetime('now')),
                message TEXT,
                details TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Database initialized: %s", db_path)
        return True
    except Exception as e:
        logger.error("DB init error: %s", e)
        return False


@contextmanager
def get_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_trade(db_path: str, trade: dict) -> Optional[int]:
    """Save a closed trade to the DB. Returns row id."""
    try:
        with get_conn(db_path) as conn:
            cur = conn.execute("""
                INSERT INTO trades (symbol,side,open_time,close_time,volume,
                    open_price,close_price,pips,pnl,close_reason,sl_pips,tp_pips,atr,adx)
                VALUES (:symbol,:side,:open_time,:close_time,:volume,
                    :open_price,:close_price,:pips,:pnl,:close_reason,:sl_pips,:tp_pips,:atr,:adx)
            """, {
                "symbol":      trade.get("symbol", ""),
                "side":        trade.get("side", ""),
                "open_time":   trade.get("open_time", ""),
                "close_time":  trade.get("close_time", ""),
                "volume":      trade.get("volume", 0),
                "open_price":  trade.get("open_price", 0),
                "close_price": trade.get("close_price", 0),
                "pips":        trade.get("pips", 0),
                "pnl":         trade.get("pnl", 0),
                "close_reason":trade.get("close_reason", ""),
                "sl_pips":     trade.get("sl_pips", 0),
                "tp_pips":     trade.get("tp_pips", 0),
                "atr":         trade.get("atr", 0),
                "adx":         trade.get("adx", 0),
            })
            return cur.lastrowid
    except Exception as e:
        logger.error("save_trade error: %s", e)
        return None


def save_equity(db_path: str, equity: float, balance: float, open_positions: int = 0):
    """Snapshot the equity curve."""
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO equity_curve (equity,balance,open_positions) VALUES (?,?,?)",
                (equity, balance, open_positions)
            )
    except Exception as e:
        logger.error("save_equity error: %s", e)


def save_error(db_path: str, message: str, details: str = ""):
    """Log an error event."""
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO errors (message,details) VALUES (?,?)",
                (message, details)
            )
    except Exception as e:
        logger.error("save_error error: %s", e)


def get_today_trades(db_path: str) -> list:
    """Return all trades closed today."""
    try:
        with get_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE date(close_time) = date('now') ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_all_trades(db_path: str, limit: int = 50) -> list:
    try:
        with get_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


class TradeDB:
    """Compatibility wrapper used by main.py."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def log_trade(self, trade: dict):
        return save_trade(self.db_path, trade)

    def log_trade_open(self, symbol, side, volume_units, entry_price,
                       sl_price, tp_price, sl_pips, tp_pips, atr, adx,
                       rsi=None, label="") -> int:
        """Called when a trade is opened (stores partial data)."""
        import sqlite3, datetime
        try:
            with get_conn(self.db_path) as conn:
                cur = conn.execute(
                    """INSERT INTO trades (symbol,side,open_time,volume,open_price,sl_pips,tp_pips,atr,adx)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (symbol, side, datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     volume_units, entry_price, sl_pips, tp_pips, atr, adx or 0)
                )
                return cur.lastrowid
        except Exception as e:
            logger.error("log_trade_open error: %s", e)
            return 0

    def update_trade_close(self, trade_id: int, close_price: float,
                           pips: float, pnl: float, reason: str = ""):
        """Update trade record when position closes."""
        import datetime
        try:
            with get_conn(self.db_path) as conn:
                conn.execute(
                    """UPDATE trades SET close_time=?,close_price=?,pips=?,pnl=?,close_reason=?
                       WHERE id=?""",
                    (datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     close_price, pips, pnl, reason, trade_id)
                )
        except Exception as e:
            logger.error("update_trade_close error: %s", e)

    def log_equity(self, equity: float, balance: float, open_pos: int = 0):
        save_equity(self.db_path, equity, balance, open_pos)

    def log_error(self, message: str, details: str = ""):
        save_error(self.db_path, message, details)

    def today_trades(self) -> list:
        return get_today_trades(self.db_path)

    def all_trades(self, limit: int = 50) -> list:
        return get_all_trades(self.db_path, limit)
