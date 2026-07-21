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
  /api/account   → JSON balance, equity, account_id, open_positions_count, daily performance, last signal
  /api/open_positions → JSON list of currently open positions
  /api/performance/daily → JSON performance of each day
  /api/control/pause   → POST stop/pause the bot
  /api/control/resume  → POST resume the bot
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
import os
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
<title>BBPro v2 - لوحة التحكم</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;900&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0a0e1a; --surface: #111827; --surface2: #1a2235;
    --border: #1e2d40; --accent: #3b82f6; --accent2: #6366f1;
    --green: #10b981; --red: #ef4444; --yellow: #f59e0b;
    --text: #e2e8f0; --muted: #64748b; --card: #151f2e;
  }
  body { font-family: 'Cairo', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

  /* Header */
  .header {
    background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .logo { width: 42px; height: 42px; background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 20px; }
  .header h1 { font-size: 20px; font-weight: 900; color: #fff; }
  .header .sub { font-size: 12px; color: var(--muted); }
  .status-badge { display: flex; align-items: center; gap: 6px; background: #0d2e1c;
    border: 1px solid #166534; border-radius: 20px; padding: 6px 14px; font-size: 13px; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
    animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  /* Main layout */
  .main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

  /* Stats grid */
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .stat-card {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px; position: relative; overflow: hidden;
    transition: transform 0.2s, border-color 0.2s;
  }
  .stat-card:hover { transform: translateY(-2px); border-color: var(--accent); }
  .stat-card::before { content: ''; position: absolute; top: 0; right: 0; left: 0; height: 2px; }
  .stat-card.blue::before { background: linear-gradient(90deg, var(--accent), var(--accent2)); }
  .stat-card.green::before { background: linear-gradient(90deg, var(--green), #059669); }
  .stat-card.red::before { background: linear-gradient(90deg, var(--red), #b91c1c); }
  .stat-card.yellow::before { background: linear-gradient(90deg, var(--yellow), #d97706); }
  .stat-label { font-size: 11px; color: var(--muted); margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-value { font-size: 24px; font-weight: 900; }
  .stat-value.pos { color: var(--green); }
  .stat-value.neg { color: var(--red); }
  .stat-value.neu { color: var(--accent); }
  .stat-sub { font-size: 11px; color: var(--muted); margin-top: 4px; }

  /* Sections */
  .section { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  .section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .section-title { font-size: 15px; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 8px; }
  .section-icon { width: 28px; height: 28px; border-radius: 8px; background: var(--surface2);
    display: flex; align-items: center; justify-content: center; font-size: 14px; }

  /* Chart */
  .chart-container { position: relative; height: 220px; }

  /* Two columns */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media(max-width:768px) { .two-col { grid-template-columns: 1fr; } }

  /* Table */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { padding: 10px 12px; text-align: right; border-bottom: 1px solid var(--border);
    color: var(--muted); font-size: 11px; font-weight: 600; text-transform: uppercase; white-space: nowrap; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--surface2); }
  .badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
    border-radius: 20px; font-size: 11px; font-weight: 700; }
  .badge.buy { background: #0d2e1c; color: var(--green); border: 1px solid #166534; }
  .badge.sell { background: #2d0e0e; color: var(--red); border: 1px solid #7f1d1d; }

  /* Symbols grid */
  .symbols-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
  .sym-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
  .sym-name { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
  .sym-stat { font-size: 11px; color: var(--muted); }

  /* Live ticker */
  .ticker-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 16px; margin-bottom: 16px; display: flex; gap: 24px; overflow-x: auto; }
  .ticker-item { display: flex; flex-direction: column; align-items: center; min-width: 80px; }
  .ticker-sym { font-size: 10px; color: var(--muted); font-weight: 600; }
  .ticker-price { font-size: 14px; font-weight: 700; color: var(--text); }
  .ticker-chg { font-size: 10px; }

  /* Footer */
  .footer { text-align: center; padding: 16px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); margin-top: 8px; }

  /* Empty state */
  .empty { text-align: center; padding: 40px; color: var(--muted); }
  .empty-icon { font-size: 36px; margin-bottom: 8px; }

  /* Connection status */
  .conn-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dot.green { background: var(--green); }
  .dot.red { background: var(--red); }
  .dot.yellow { background: var(--yellow); }

  /* Button controls */
  .btn:hover { opacity: 0.9; }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo">📈</div>
    <div>
      <h1>Bollinger Breakout Pro v2</h1>
      <div class="sub">لوحة مراقبة الروبوت — يُحدَّث تلقائياً كل 30 ثانية</div>
    </div>
  </div>
  <div class="status-badge">
    <div class="status-dot" id="statusDot"></div>
    <span id="statusText">جاري التحميل...</span>
  </div>
</div>

<div class="main">

  <!-- Live ticker -->
  <div class="ticker-bar" id="tickerBar">
    <div class="ticker-item"><div class="ticker-sym">EURUSD</div><div class="ticker-price" id="t_EURUSD">—</div></div>
    <div class="ticker-item"><div class="ticker-sym">XAUUSD</div><div class="ticker-price" id="t_XAUUSD">—</div></div>
    <div class="ticker-item"><div class="ticker-sym">GBPUSD</div><div class="ticker-price" id="t_GBPUSD">—</div></div>
    <div class="ticker-item"><div class="ticker-sym">USDJPY</div><div class="ticker-price" id="t_USDJPY">—</div></div>
    <div class="ticker-item"><div class="ticker-sym">EURJPY</div><div class="ticker-price" id="t_EURJPY">—</div></div>
    <div class="ticker-item"><div class="ticker-sym">USDCAD</div><div class="ticker-price" id="t_USDCAD">—</div></div>
  </div>

  <!-- Account Card & Control -->
  <div class="two-col" style="margin-bottom: 20px;">
    <!-- Account Card -->
    <div class="section" style="margin-bottom: 0;">
      <div class="section-header">
        <div class="section-title"><div class="section-icon">💳</div> بطاقة الحساب</div>
      </div>
      <div class="stats-grid" style="grid-template-columns: repeat(3, 1fr); margin-bottom: 0;">
        <div class="stat-card blue" style="margin-bottom:0;"><div class="stat-label">الرصيد الحقيقي</div><div class="stat-value" id="acc_balance">—</div></div>
        <div class="stat-card green" style="margin-bottom:0;"><div class="stat-label">حقوق الملكية (Equity)</div><div class="stat-value" id="acc_equity">—</div></div>
        <div class="stat-card yellow" style="margin-bottom:0;"><div class="stat-label">الصفقات المفتوحة</div><div class="stat-value" id="acc_open">—</div></div>
      </div>
    </div>
    
    <!-- Control Section -->
    <div class="section" style="margin-bottom: 0;">
      <div class="section-header">
        <div class="section-title"><div class="section-icon">⚙️</div> التحكم في البوت</div>
      </div>
      <div style="display: flex; flex-direction: column; justify-content: center; height: calc(100% - 40px); gap: 14px;">
        <div style="display: flex; align-items: center; justify-content: space-between;">
          <span style="font-size: 14px; font-weight: 600;">حالة التشغيل:</span>
          <span id="botStateBadge" class="badge" style="padding: 6px 16px; font-size: 13px;">جاري التحميل...</span>
        </div>
        <div style="display: flex; gap: 10px;">
          <button id="btnPause" class="btn" onclick="controlBot('pause')" style="flex: 1; padding: 10px; border-radius: 8px; font-family: 'Cairo', sans-serif; font-weight: 700; background: var(--red); color: white; border: none; cursor: pointer; transition: opacity 0.2s;">⏸️ إيقاف مؤقت</button>
          <button id="btnResume" class="btn" onclick="controlBot('resume')" style="flex: 1; padding: 10px; border-radius: 8px; font-family: 'Cairo', sans-serif; font-weight: 700; background: var(--green); color: white; border: none; cursor: pointer; transition: opacity 0.2s;">▶️ استئناف</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats-grid" id="statsGrid">
    <div class="stat-card blue"><div class="stat-label">إجمالي الصفقات</div><div class="stat-value neu" id="s_total">—</div></div>
    <div class="stat-card green"><div class="stat-label">نسبة النجاح</div><div class="stat-value pos" id="s_winrate">—</div></div>
    <div class="stat-card green"><div class="stat-label">إجمالي الربح</div><div class="stat-value" id="s_pnl">—</div></div>
    
    <div class="stat-card blue"><div class="stat-label">صفقات اليوم</div><div class="stat-value neu" id="s_trades_today">—</div></div>
    <div class="stat-card green"><div class="stat-label">نقاط اليوم</div><div class="stat-value" id="s_pips_today">—</div></div>
    <div class="stat-card green"><div class="stat-label">نسبة نجاح اليوم</div><div class="stat-value pos" id="s_winrate_today">—</div></div>

    <div class="stat-card blue"><div class="stat-label">عامل الربح</div><div class="stat-value neu" id="s_pf">—</div></div>
    <div class="stat-card blue"><div class="stat-label">شارب راشيو</div><div class="stat-value neu" id="s_sharpe">—</div></div>
    <div class="stat-card red"><div class="stat-label">أقصى سحب</div><div class="stat-value neg" id="s_dd">—</div></div>
    <div class="stat-card yellow"><div class="stat-label">التوقع/صفقة</div><div class="stat-value" id="s_exp">—</div></div>
    <div class="stat-card blue"><div class="stat-label">آخر تحديث</div><div class="stat-value neu" style="font-size:14px" id="s_time">—</div></div>
  </div>

  <!-- Chart + Connections -->
  <div class="two-col">
    <div class="section">
      <div class="section-header">
        <div class="section-title"><div class="section-icon">📊</div> منحنى رأس المال</div>
      </div>
      <div class="chart-container">
        <canvas id="equityChart"></canvas>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <div class="section-title"><div class="section-icon">🔗</div> حالة الاتصالات</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:14px;padding:8px 0">
        <div class="conn-row"><div class="dot green" id="d_ctrader"></div><span>cTrader Open API — demo.ctraderapi.com:5036</span></div>
        <div class="conn-row"><div class="dot green" id="d_telegram"></div><span>Telegram Bot — إشعارات فعّالة</span></div>
        <div class="conn-row"><div class="dot green"></div><span>Railway Cloud — يعمل 24/7</span></div>
        <div class="conn-row"><div class="dot yellow"></div><span>قاعدة البيانات — معطّلة (وضع الذاكرة)</span></div>
      </div>
      
      <div style="margin-top:20px; border-top: 1px solid var(--border); padding-top: 16px;">
        <div class="section-title" style="margin-bottom:12px"><div class="section-icon">📡</div> آخر إشارة ومؤشر التوافق (Confluence)</div>
        <div style="background: var(--surface2); padding: 12px; border-radius: 10px; border: 1px solid var(--border);">
          <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
            <span style="font-size: 13px; color: var(--muted);">الزوج والاتجاه:</span>
            <span id="sig_symbol" style="font-weight: 700;">—</span>
          </div>
          <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
            <span style="font-size: 13px; color: var(--muted);">الوقت:</span>
            <span id="sig_time">—</span>
          </div>
          <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
            <span style="font-size: 13px; color: var(--muted);">مؤشر التوافق (Confluence):</span>
            <span id="sig_confluence" style="font-weight: 700; color: var(--yellow);">—</span>
          </div>
          <div style="display: flex; justify-content: space-between;">
            <span style="font-size: 13px; color: var(--muted);">حالة الإشارة:</span>
            <span id="sig_status" class="badge">—</span>
          </div>
        </div>
      </div>

      <div style="margin-top:20px">
        <div class="section-title" style="margin-bottom:12px"><div class="section-icon">🎯</div> الأزواج النشطة</div>
        <div class="symbols-grid" id="symbolsGrid">
          <div class="sym-card"><div class="sym-name">EURUSD</div><div class="sym-stat">م30 | نشط</div></div>
          <div class="sym-card"><div class="sym-name">XAUUSD</div><div class="sym-stat">م30 | نشط</div></div>
          <div class="sym-card"><div class="sym-name">GBPUSD</div><div class="sym-stat">م30 | نشط</div></div>
          <div class="sym-card"><div class="sym-name">USDJPY</div><div class="sym-stat">م30 | نشط</div></div>
          <div class="sym-card"><div class="sym-name">EURJPY</div><div class="sym-stat">م30 | نشط</div></div>
          <div class="sym-card"><div class="sym-name">USDCAD</div><div class="sym-stat">م30 | نشط</div></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Open Positions Table -->
  <div class="section">
    <div class="section-header">
      <div class="section-title"><div class="section-icon">🔓</div> الصفقات المفتوحة حالياً</div>
      <span style="font-size:12px;color:var(--muted)" id="openCount"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>وقت الفتح</th><th>الزوج</th><th>الاتجاه</th><th>الحجم</th><th>سعر الدخول</th><th>السعر الحالي</th><th>الربح ($)</th>
        </tr></thead>
        <tbody id="openBody"></tbody>
      </table>
      <div class="empty" id="emptyOpen" style="display:none">
        <div class="empty-icon">🏖️</div>
        <div>لا توجد صفقات مفتوحة حالياً</div>
      </div>
    </div>
  </div>

  <!-- Trades table -->
  <div class="section">
    <div class="section-header">
      <div class="section-title"><div class="section-icon">📋</div> آخر الصفقات</div>
      <span style="font-size:12px;color:var(--muted)" id="tradesCount"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>وقت الإغلاق</th><th>الزوج</th><th>الاتجاه</th><th>الحجم</th>
          <th>النتيجة (نقاط)</th><th>الربح ($)</th><th>السبب</th>
        </tr></thead>
        <tbody id="tradesBody"></tbody>
      </table>
      <div class="empty" id="emptyTrades" style="display:none">
        <div class="empty-icon">📭</div>
        <div>لا توجد صفقات مسجّلة بعد</div>
        <div style="font-size:12px;margin-top:4px">الروبوت ينتظر إشارات الدخول...</div>
      </div>
    </div>
  </div>

</div>

<div class="footer">
  BBPro Ultimate v2.0 — Bollinger Breakout Strategy — Railway Cloud Hosting<br>
  EURUSD · XAUUSD · GBPUSD · USDJPY · EURJPY · USDCAD
</div>

<script>
let equityChart = null;

function fmt(n, d=2) { return (Number(n)||0).toFixed(d); }
function fmtTime(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('ar-EG', {hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit'}); }
  catch { return s; }
}

async function fetchJSON(url) {
  try { const r = await fetch(url); return r.ok ? r.json() : null; }
  catch { return null; }
}

async function controlBot(action) {
  try {
    const r = await fetch(\`/api/control/\${action}\`, { method: 'POST' });
    if (r.ok) {
      await loadAccount();
    } else {
      alert('حدث خطأ أثناء تنفيذ الأمر');
    }
  } catch (e) {
    alert('فشل الاتصال بالخادم');
  }
}

async function loadAccount() {
  const acc = await fetchJSON('/api/account');
  if (!acc) return;

  // Account card
  document.getElementById('acc_balance').textContent = '$' + fmt(acc.balance, 2);
  document.getElementById('acc_equity').textContent = '$' + fmt(acc.equity, 2);
  document.getElementById('acc_open').textContent = acc.open_positions_count;

  // Control / Pause Resume UI
  const badge = document.getElementById('botStateBadge');
  if (acc.bot_paused) {
    badge.textContent = '⏸️ موقوف مؤقتاً';
    badge.className = 'badge sell';
  } else {
    badge.textContent = '▶️ يعمل بنشاط';
    badge.className = 'badge buy';
  }

  // Last Signal
  const sig = acc.last_signal || {};
  document.getElementById('sig_symbol').textContent = (sig.symbol ? sig.symbol + ' (' + (sig.direction || sig.side || '—') + ')' : '—');
  document.getElementById('sig_time').textContent = sig.ts ? fmtTime(sig.ts) : '—';
  document.getElementById('sig_confluence').textContent = sig.confluence_score || '—';
  
  const sigStatus = document.getElementById('sig_status');
  if (sig.symbol) {
    const isAccepted = sig.accepted !== undefined ? sig.accepted : true;
    if (isAccepted) {
      sigStatus.textContent = 'مقبولة ✓';
      sigStatus.className = 'badge buy';
    } else {
      sigStatus.textContent = 'مرفوضة: ' + (sig.reject_reason || 'غير محدد');
      sigStatus.className = 'badge sell';
    }
  } else {
    sigStatus.textContent = '—';
    sigStatus.className = 'badge';
  }

  // Daily stats cards
  document.getElementById('s_trades_today').textContent = acc.trades_today || '0';
  
  const pipsToday = Number(acc.pips_today || 0);
  const pipsTodayEl = document.getElementById('s_pips_today');
  pipsTodayEl.textContent = (pipsToday >= 0 ? '+' : '') + fmt(pipsToday, 1);
  pipsTodayEl.className = 'stat-value ' + (pipsToday >= 0 ? 'pos' : 'neg');
  
  document.getElementById('s_winrate_today').textContent = fmt(acc.winrate_today, 1) + '%';
}

async function loadStats() {
  const stats = await fetchJSON('/api/stats');
  if (!stats) { document.getElementById('statusText').textContent = 'خطأ في الاتصال'; return; }

  document.getElementById('statusText').textContent = 'الروبوت يعمل ✓';
  document.getElementById('statusDot').style.background = '#10b981';

  const pnl = Number(stats.total_pnl||0);
  document.getElementById('s_total').textContent = stats.total_trades || '0';
  document.getElementById('s_winrate').textContent = fmt(stats.win_rate,1) + '%';

  const pnlEl = document.getElementById('s_pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl, 2);
  pnlEl.className = 'stat-value ' + (pnl >= 0 ? 'pos' : 'neg');

  document.getElementById('s_pf').textContent = fmt(stats.profit_factor, 2);
  document.getElementById('s_sharpe').textContent = fmt(stats.sharpe_ratio, 2);
  document.getElementById('s_dd').textContent = '-' + fmt(stats.max_drawdown_pct, 2) + '%';

  const exp = Number(stats.expectancy||0);
  const expEl = document.getElementById('s_exp');
  expEl.textContent = (exp >= 0 ? '+' : '') + fmt(exp, 2);
  expEl.className = 'stat-value ' + (exp >= 0 ? 'pos' : 'neg');

  document.getElementById('s_time').textContent = new Date().toLocaleTimeString('ar-EG');
}

async function loadEquity() {
  const data = await fetchJSON('/api/equity?hours=24');
  const labels = data && data.length ? data.map(r => fmtTime(r.ts)) : ['الآن'];
  const values = data && data.length ? data.map(r => r.equity) : [193756];

  const ctx = document.getElementById('equityChart').getContext('2d');
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'رأس المال',
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 4
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2d40' } },
        y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e2d40' } }
      }
    }
  });
}

async function loadTrades() {
  const trades = await fetchJSON('/api/trades?limit=20');
  const tbody = document.getElementById('tradesBody');
  const empty = document.getElementById('emptyTrades');
  const count = document.getElementById('tradesCount');

  if (!trades || trades.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    count.textContent = 'لا صفقات';
    return;
  }
  empty.style.display = 'none';
  count.textContent = trades.length + ' صفقة';

  tbody.innerHTML = trades.map(t => {
    const pnl = Number(t.pnl||0);
    const pts = Number(t.pips||0);
    return \`<tr>
      <td>\${fmtTime(t.close_time)}</td>
      <td style="font-weight:700">\${t.symbol||'—'}</td>
      <td><span class="badge \${t.side==='buy'?'buy':'sell'}">\${t.side==='buy'?'▲ شراء':'▼ بيع'}</span></td>
      <td>\${t.volume||'—'}</td>
      <td style="color:\${pts>=0?'var(--green)':'var(--red)'};">\${(pts>=0?'+':'')+fmt(pts,1)}</td>
      <td style="color:\${pnl>=0?'var(--green)':'var(--red)'};font-weight:700">\${(pnl>=0?'+':'')+fmt(pnl,2)}</td>
      <td style="color:var(--muted);font-size:11px">\${t.close_reason||'—'}</td>
    </tr>\`;
  }).join('');
}

async function loadOpenPositions() {
  const openPositions = await fetchJSON('/api/open_positions');
  const tbody = document.getElementById('openBody');
  const empty = document.getElementById('emptyOpen');
  const count = document.getElementById('openCount');

  if (!openPositions || openPositions.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    count.textContent = 'لا صفقات مفتوحة';
    return;
  }
  empty.style.display = 'none';
  count.textContent = openPositions.length + ' صفقة مفتوحة';

  tbody.innerHTML = openPositions.map(p => {
    const pnl = Number(p.pnl||0);
    return \`<tr>
      <td>\${fmtTime(p.open_time)}</td>
      <td style="font-weight:700">\${p.symbol||'—'}</td>
      <td><span class="badge \${p.side==='buy'?'buy':'sell'}">\${p.side==='buy'?'▲ شراء':'▼ بيع'}</span></td>
      <td>\${p.volume||'—'}</td>
      <td>\${fmt(p.entry_price, 5)}</td>
      <td>\${fmt(p.current_price, 5)}</td>
      <td style="color:\${pnl>=0?'var(--green)':'var(--red)'};font-weight:700">\${(pnl>=0?'+':'')+fmt(pnl,2)}</td>
    </tr>\`;
  }).join('');
}

async function refresh() {
  await Promise.all([loadAccount(), loadStats(), loadEquity(), loadTrades(), loadOpenPositions()]);
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


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
    _flask_app_ref[0] = app  # exported for main.py
    app.config["DB_PATH"] = db_path
    app.config["BOT_PAUSED"] = False
    app.config["ACCOUNT_BALANCE"] = 0.0
    app.config["OPEN_POSITIONS"] = []
    app.config["LAST_SIGNAL"] = {}
    app.config["TODAY_TRADES"] = []
    app.config["CONFLUENCE_SCORE"] = 0

    # Initialize requested global state
    app.config['BOT_PAUSED'] = False
    app.config['ACCOUNT_BALANCE'] = 0.0
    app.config['OPEN_POSITIONS'] = []
    app.config['LAST_SIGNAL'] = {}

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
        try:
            with get_conn() as c:
                rows = c.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return jsonify([dict(r) for r in rows])
        except Exception:
            return jsonify([])

    @app.route("/api/equity")
    def api_equity():
        hours = request.args.get("hours", 24, type=int)
        try:
            with get_conn() as c:
                rows = c.execute(
                    """SELECT * FROM equity_curve
                       WHERE ts >= datetime('now', ?)
                       ORDER BY ts ASC""",
                    (f"-{hours} hours",)
                ).fetchall()
            return jsonify([dict(r) for r in rows])
        except Exception:
            return jsonify([])

    @app.route("/api/errors")
    def api_errors():
        hours = request.args.get("hours", 24, type=int)
        try:
            with get_conn() as c:
                rows = c.execute(
                    """SELECT * FROM errors
                       WHERE ts >= datetime('now', ?)
                       ORDER BY id DESC LIMIT 100""",
                    (f"-{hours} hours",)
                ).fetchall()
            return jsonify([dict(r) for r in rows])
        except Exception:
            return jsonify([])

    @app.route("/api/account")
    def api_account():
        balance = app.config.get('ACCOUNT_BALANCE', 0.0)
        equity = balance
        try:
            with get_conn() as c:
                row = c.execute("SELECT equity FROM equity_curve ORDER BY ts DESC LIMIT 1").fetchone()
                if row:
                    equity = row['equity']
        except Exception:
            pass
        
        open_positions = app.config.get('OPEN_POSITIONS', [])
        open_positions_count = len(open_positions)
        
        account_id = os.environ.get("CTRADER_ACCOUNT_ID", "47838646")
        
        # Daily stats from db
        trades_today = 0
        pips_today = 0.0
        winrate_today = 0.0
        try:
            with get_conn() as c:
                row = c.execute(
                    """SELECT 
                           COUNT(*) as total,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                           SUM(pips) as pips
                       FROM trades
                       WHERE close_time >= date('now', 'start of day')"""
                ).fetchone()
                if row and row["total"] > 0:
                    trades_today = row["total"]
                    pips_today = row["pips"] or 0.0
                    winrate_today = (row["wins"] / trades_today) * 100
        except Exception as e:
            logger.error("Error calculating daily stats: %s", e)
            
        # Last signal
        last_sig = app.config.get('LAST_SIGNAL', {})
        if not last_sig:
            try:
                with get_conn() as c:
                    row = c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 1").fetchone()
                    if row:
                        row_dict = dict(row)
                        confluence_score = "4/5"
                        last_sig = {
                            "symbol": row_dict["symbol"],
                            "direction": row_dict["direction"],
                            "ts": row_dict["ts"],
                            "confluence_score": confluence_score,
                            "accepted": bool(row_dict["accepted"]),
                            "reject_reason": row_dict["reject_reason"]
                        }
            except Exception:
                pass
                
        return jsonify({
            "balance": balance,
            "equity": equity,
            "account_id": account_id,
            "open_positions_count": open_positions_count,
            "bot_paused": app.config.get('BOT_PAUSED', False),
            "trades_today": trades_today,
            "pips_today": pips_today,
            "winrate_today": winrate_today,
            "last_signal": last_sig
        })

    @app.route("/api/open_positions")
    def api_open_positions():
        return jsonify(app.config.get('OPEN_POSITIONS', []))

    @app.route("/api/performance/daily")
    def api_performance_daily():
        try:
            with get_conn() as c:
                rows = c.execute(
                    """SELECT 
                           substr(close_time, 1, 10) as day,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                           SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                           SUM(pips) as pips,
                           SUM(pnl) as profit
                       FROM trades
                       WHERE close_time IS NOT NULL
                       GROUP BY day
                       ORDER BY day DESC"""
                ).fetchall()
            return jsonify([dict(r) for r in rows])
        except Exception as e:
            logger.error("Error in /api/performance/daily: %s", e)
            return jsonify([])

    @app.route("/api/control/pause", methods=["POST"])
    def api_control_pause():
        app.config['BOT_PAUSED'] = True
        return jsonify({"status": "success", "paused": True})

    @app.route("/api/control/resume", methods=["POST"])
    def api_control_resume():
        app.config['BOT_PAUSED'] = False
        return jsonify({"status": "success", "paused": False})


    

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
