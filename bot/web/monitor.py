"""
web/monitor.py
==============
Lightweight Flask web UI for monitoring the bot from any browser
(including your phone's browser).

Routes:
  /              → Dashboard (equity curve, key stats, recent trades)
  /api/stats     → JSON performance summary
  /api/trades    → JSON list of recent trades
  /api/equity    → JSON equity curve (last 24h)
  /api/errors    → JSON recent errors
  /health        → JSON health check

Run standalone:
  python web/monitor.py --db bbpro.db --port 5100

Or embed in main.py:
  from web.monitor import start_monitor
  start_monitor(db_path="bbpro.db", port=5100)
"""

import argparse
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ===========================================================================
#  HTML TEMPLATE (single-file dashboard)
# ===========================================================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BBPro v2 - Monitor</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, system-ui, sans-serif;
    background: #0d1117; color: #c9d1d9;
    padding: 16px; max-width: 1200px; margin: 0 auto;
}
h1 { color: #58a6ff; margin-bottom: 8px; font-size: 22px; }
.subtitle { color: #8b949e; margin-bottom: 20px; font-size: 13px; }
.grid {
    display: grid; gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    margin-bottom: 20px;
}
.card {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 14px;
}
.card .label { color: #8b949e; font-size: 11px; text-transform: uppercase; }
.card .value { color: #58a6ff; font-size: 22px; font-weight: bold; margin-top: 4px; }
.card .value.positive { color: #3fb950; }
.card .value.negative { color: #f85149; }
.section {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 14px; margin-bottom: 16px;
}
.section h2 { color: #58a6ff; font-size: 16px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { padding: 6px 8px; text-align: right; border-bottom: 1px solid #30363d; }
th { color: #8b949e; font-weight: 600; text-transform: uppercase; font-size: 10px; }
tr:hover { background: #1c2128; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 10px; }
.tag.buy { background: #1a4731; color: #3fb950; }
.tag.sell { background: #4a1f1f; color: #f85149; }
.refresh { color: #8b949e; font-size: 11px; margin-top: 4px; }
canvas { width: 100% !important; height: 200px !important; }
</style>
</head>
<body>
<h1>🤖 Bollinger Breakout Pro v2</h1>
<div class="subtitle">Monitor — يُحدّث كل 30 ثانية</div>

<div class="grid" id="stats"></div>

<div class="section">
<h2>📊 منحنى رأس المال (24 ساعة)</h2>
<canvas id="equityChart"></canvas>
</div>

<div class="section">
<h2>📋 آخر 20 صفقة</h2>
<table id="tradesTable">
<thead><tr>
<th>وقت الإغلاق</th><th>الرمز</th><th>الاتجاه</th><th>الحجم</th>
<th>النتيجة (نقاط)</th><th>الربح</th><th>السبب</th>
</tr></thead>
<tbody></tbody>
</table>
</div>

<div class="refresh">آخر تحديث: <span id="lastUpdate">—</span></div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
let chart;
async function fetchJSON(url) {
    const r = await fetch(url);
    return r.json();
}
function fmt(n, d=2) { return Number(n||0).toFixed(d); }
function fmtTime(s) {
    if (!s) return '—';
    return new Date(s).toLocaleString('ar-EG', {hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit'});
}

async function refresh() {
    try {
        const [stats, trades, equity] = await Promise.all([
            fetchJSON('/api/stats'),
            fetchJSON('/api/trades?limit=20'),
            fetchJSON('/api/equity?hours=24')
        ]);

        // Stats cards
        const cards = [
            {label:'إجمالي الصفقات', value: stats.total_trades},
            {label:'نسبة النجاح', value: fmt(stats.win_rate,1)+'%'},
            {label:'إجمالي الربح', value: fmt(stats.total_pnl,2), cls: stats.total_pnl>=0?'positive':'negative'},
            {label:'عامل الربح', value: fmt(stats.profit_factor,2)},
            {label:'شارب', value: fmt(stats.sharpe_ratio,2)},
            {label:'سورتينو', value: fmt(stats.sortino_ratio,2)},
            {label:'أقصى خسارة', value: fmt(stats.max_drawdown_pct,2)+'%', cls:'negative'},
            {label:'التوقع/صفقة', value: fmt(stats.expectancy,2), cls: stats.expectancy>=0?'positive':'negative'},
        ];
        document.getElementById('stats').innerHTML = cards.map(c =>
            `<div class="card"><div class="label">${c.label}</div>
             <div class="value ${c.cls||''}">${c.value}</div></div>`
        ).join('');

        // Trades table
        document.querySelector('#tradesTable tbody').innerHTML =
            trades.map(t => `
                <tr>
                    <td>${fmtTime(t.close_time||t.open_time)}</td>
                    <td>${t.symbol}</td>
                    <td><span class="tag ${t.side}">${t.side.toUpperCase()}</span></td>
                    <td>${fmt(t.volume_units,2)}</td>
                    <td>${t.pips_result!==null?fmt(t.pips_result,1):'—'}</td>
                    <td>${t.profit_amount!==null?fmt(t.profit_amount,2):'—'}</td>
                    <td>${t.close_reason||'مفتوحة'}</td>
                </tr>
            `).join('') || '<tr><td colspan="7">لا توجد صفقات بعد</td></tr>';

        // Equity chart
        const ctx = document.getElementById('equityChart').getContext('2d');
        const labels = equity.map(e => fmtTime(e.ts));
        const data = equity.map(e => e.equity);
        if (chart) chart.destroy();
        chart = new Chart(ctx, {
            type: 'line',
            data: { labels, datasets: [{
                label: 'Equity', data, borderColor: '#58a6ff',
                backgroundColor: 'rgba(88,166,255,0.1)', fill: true, tension: 0.1
            }]},
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#8b949e', maxTicksLimit: 6 }, grid: { color: '#21262d' } },
                    y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
                }
            }
        });

        document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString('ar-EG');
    } catch (e) {
        console.error('Refresh failed:', e);
    }
}
refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""


# ===========================================================================
#  FLASK APP FACTORY
# ===========================================================================
def create_app(db_path: str = "bbpro.db"):
    """Create a Flask app bound to the given SQLite database."""
    try:
        from flask import Flask, jsonify, request, Response
    except ImportError:
        logger.error("Flask not installed - run: pip install flask")
        raise

    app = Flask(__name__)
    app.config["DB_PATH"] = db_path

    def get_conn():
        conn = sqlite3.connect(app.config["DB_PATH"])
        conn.row_factory = sqlite3.Row
        return conn

    @app.route("/")
    def dashboard():
        return Response(DASHBOARD_HTML, mimetype="text/html")

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/stats")
    def api_stats():
        from analytics.performance import PerformanceAnalyzer
        report = PerformanceAnalyzer(app.config["DB_PATH"]).compute()
        return jsonify(report.to_dict())

    @app.route("/api/trades")
    def api_trades():
        limit = request.args.get("limit", 50, type=int)
        with get_conn() as c:
            rows = c.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/equity")
    def api_equity():
        hours = request.args.get("hours", 24, type=int)
        with get_conn() as c:
            rows = c.execute(
                """SELECT * FROM equity_curve
                   WHERE ts >= datetime('now', ?)
                   ORDER BY ts ASC""",
                (f"-{hours} hours",)
            ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route("/api/errors")
    def api_errors():
        hours = request.args.get("hours", 24, type=int)
        with get_conn() as c:
            rows = c.execute(
                """SELECT * FROM errors
                   WHERE ts >= datetime('now', ?)
                   ORDER BY id DESC LIMIT 100""",
                (f"-{hours} hours",)
            ).fetchall()
        return jsonify([dict(r) for r in rows])

    return app


# ===========================================================================
#  BACKGROUND STARTER (for embedding in main.py)
# ===========================================================================
def start_monitor(db_path: str = "bbpro.db", port: int = 5100,
                  host: str = "0.0.0.0") -> threading.Thread:
    """Start the monitor in a background daemon thread."""
    app = create_app(db_path)
    try:
        from waitress import serve
        def _run():
            logger.info("Web monitor (waitress) on http://%s:%d", host, port)
            serve(app, host=host, port=port)
    except ImportError:
        def _run():
            logger.info("Web monitor (flask) on http://%s:%d", host, port)
            app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ===========================================================================
#  STANDALONE ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="bbpro.db")
    p.add_argument("--port", type=int, default=5100)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print(f"Dashboard: http://localhost:{args.port}")
    start_monitor(args.db, args.port, args.host)
    # Keep main thread alive
    import time
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nStopped.")
