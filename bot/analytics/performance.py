"""
analytics/performance.py
========================
Performance analytics for the Bollinger Breakout Pro bot.

Computes:
  - Total P/L
  - Win rate (%)
  - Profit factor (gross profit / gross loss)
  - Sharpe ratio (per-trade)
  - Sortino ratio (downside-only)
  - Max drawdown (%)
  - Average win / average loss
  - Expectancy per trade
  - Total trades / winning / losing

Reads from the SQLite database populated by storage/database.py.
"""

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PerformanceReport:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0          # %
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0     # gross_profit / gross_loss
    sharpe_ratio: float = 0.0      # per-trade
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    expectancy: float = 0.0        # avg P/L per trade
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_hold_time_min: float = 0.0
    last_trade_time: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def format_text(self) -> str:
        return (
            f"=== Performance Report ===\n"
            f"Total trades:    {self.total_trades}\n"
            f"Win/Loss/BE:     {self.winning_trades}/{self.losing_trades}/{self.breakeven_trades}\n"
            f"Win rate:        {self.win_rate:.1f}%\n"
            f"Total P/L:       {self.total_pnl:+.2f}\n"
            f"Avg win:         {self.avg_win:+.2f}\n"
            f"Avg loss:        {self.avg_loss:+.2f}\n"
            f"Profit factor:   {self.profit_factor:.2f}\n"
            f"Sharpe (trade):  {self.sharpe_ratio:.2f}\n"
            f"Sortino:         {self.sortino_ratio:.2f}\n"
            f"Max drawdown:    {self.max_drawdown_pct:.2f}%\n"
            f"Expectancy:      {self.expectancy:+.2f}/trade\n"
            f"Best/Worst:      {self.best_trade:+.2f} / {self.worst_trade:+.2f}\n"
            f"Avg hold:        {self.avg_hold_time_min:.1f} min\n"
            f"Last trade:      {self.last_trade_time}"
        )


class PerformanceAnalyzer:
    """Reads trades from SQLite and computes performance metrics."""

    def __init__(self, db_path: str = "bbpro.db"):
        self.db_path = Path(db_path)

    def compute(self) -> PerformanceReport:
        if not self.db_path.exists():
            return PerformanceReport()

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT * FROM trades
                   WHERE close_time IS NOT NULL
                   ORDER BY close_time ASC"""
            ).fetchall()
            equity_rows = conn.execute(
                "SELECT equity FROM equity_curve ORDER BY ts ASC"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return PerformanceReport()

        pnl_list = [float((r["pnl"] if "pnl" in r.keys() else r["profit_amount"]) or 0) for r in rows]
        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p < 0]
        breakeven = [p for p in pnl_list if p == 0]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        total_pnl = sum(pnl_list)

        # Profit factor
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

        # Sharpe (per-trade, annualization skipped)
        arr = np.array(pnl_list)
        mean = arr.mean()
        std = arr.std(ddof=1) if len(arr) > 1 else 0
        sharpe = (mean / std) if std > 0 else 0

        # Sortino (downside-only std)
        downside = arr[arr < 0]
        if len(downside) > 1:
            downside_std = downside.std(ddof=1)
            sortino = (mean / downside_std) if downside_std > 0 else 0
        else:
            sortino = 0

        # Max drawdown from equity curve
        max_dd = 0.0
        if equity_rows:
            eq = [float(r["equity"]) for r in equity_rows]
            peak = eq[0]
            for v in eq:
                if v > peak:
                    peak = v
                if peak > 0:
                    dd = (peak - v) / peak * 100
                    if dd > max_dd:
                        max_dd = dd

        # Expectancy
        expectancy = mean

        # Hold time
        hold_mins = []
        for r in rows:
            try:
                from datetime import datetime
                o = datetime.fromisoformat(r["open_time"])
                c = datetime.fromisoformat(r["close_time"])
                hold_mins.append((c - o).total_seconds() / 60)
            except Exception:
                pass
        avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0

        return PerformanceReport(
            total_trades=len(rows),
            winning_trades=len(wins),
            losing_trades=len(losses),
            breakeven_trades=len(breakeven),
            win_rate=(len(wins) / len(rows) * 100) if rows else 0,
            total_pnl=total_pnl,
            avg_win=(gross_profit / len(wins)) if wins else 0,
            avg_loss=(-gross_loss / len(losses)) if losses else 0,
            profit_factor=pf,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=max_dd,
            expectancy=expectancy,
            best_trade=max(pnl_list) if pnl_list else 0,
            worst_trade=min(pnl_list) if pnl_list else 0,
            avg_hold_time_min=avg_hold,
            last_trade_time=rows[-1]["close_time"],
        )
