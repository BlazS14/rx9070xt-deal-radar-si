"""Lightweight local web dashboard (zero extra server dependencies, runs on user's PC)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import RadarConfig
from .storage import DealStorage

_HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>RX 9070 XT Deal Radar — Slovenia</title>
  <style>
    :root {
      --bg: #0b1020; --panel: #131a2e; --border: #243050;
      --text: #e8eefc; --muted: #8ea0c6; --accent: #38bdf8;
      --steal: #ef4444; --hot: #f59e0b; --good: #10b981;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 24px 20px 48px; }
    header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 20px; }
    h1 { margin: 0; font-size: 1.45rem; letter-spacing: -0.02em; }
    .sub { color: var(--muted); font-size: 0.9rem; margin-top: 4px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-bottom: 22px; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
    .kpi-label { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }
    .kpi-val { font-size: 1.7rem; font-weight: 700; margin-top: 6px; }
    .controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }
    input[type="text"], input[type="number"] {
      background: #0e1528; border: 1px solid var(--border); color: var(--text);
      padding: 8px 10px; border-radius: 8px; font-size: 0.9rem;
    }
    label { display: inline-flex; align-items: center; gap: 6px; font-size: 0.88rem; color: var(--muted); cursor: pointer; }
    button {
      background: var(--accent); color: #051120; border: 0; padding: 8px 14px;
      border-radius: 8px; font-weight: 600; cursor: pointer;
    }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    th, td { padding: 10px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.05em; }
    tr:hover td { background: rgba(56, 189, 248, 0.05); }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 700; }
    .STEAL { background: rgba(239,68,68,.2); color: #fca5a5; border: 1px solid rgba(239,68,68,.5); }
    .HOT { background: rgba(245,158,11,.2); color: #fcd34d; border: 1px solid rgba(245,158,11,.5); }
    .GOOD { background: rgba(16,185,129,.2); color: #6ee7b7; border: 1px solid rgba(16,185,129,.5); }
    .TRACKED { background: rgba(148,163,184,.15); color: #cbd5e1; border: 1px solid rgba(148,163,184,.3); }
    .openbox { background: rgba(168,85,247,.2); color: #d8b4fe; border: 1px solid rgba(168,85,247,.5); }
    .promo { background: rgba(56,189,248,.18); color: #7dd3fc; border: 1px solid rgba(56,189,248,.45); font-family: monospace; }
    .oos { opacity: 0.55; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .two-col { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; margin-top: 22px; }
    @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
    .ok { color: #6ee7b7; } .partial { color: #fcd34d; } .blocked, .error { color: #fca5a5; }
    .small { color: var(--muted); font-size: 0.78rem; }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>RX 9070 XT Deal Radar — Slovenia</h1>
        <div class="sub">Tracks SI + EU-to-Slovenia listings, Open-Box / B-Ware drops, and stacked promo codes</div>
      </div>
      <div>
        <button onclick="loadData()">Refresh</button>
      </div>
    </header>

    <div class="grid" id="kpis"></div>

    <div class="card">
      <div class="controls">
        <input id="q" type="text" placeholder="Filter model, store, promo..." oninput="renderDeals()" />
        <input id="maxPrice" type="number" placeholder="Max € landed" style="width:140px" oninput="renderDeals()" />
        <label><input id="onlyStock" type="checkbox" checked onchange="renderDeals()" /> In stock only</label>
        <label><input id="onlyOpenBox" type="checkbox" onchange="renderDeals()" /> Open-box / B-Ware only</label>
      </div>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th>Tier</th>
              <th>Landed (SI)</th>
              <th>GPU & Store</th>
              <th>Condition</th>
              <th>Stacked Discounts / Promo</th>
              <th>Score</th>
              <th>Stock</th>
            </tr>
          </thead>
          <tbody id="dealsBody"></tbody>
        </table>
      </div>
    </div>

    <div class="two-col">
      <div class="card">
        <h3 style="margin-top:0">Recent Alerts</h3>
        <div id="alertsList" class="small">Loading...</div>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Watcher Health</h3>
        <div id="healthList" class="small">Loading...</div>
      </div>
    </div>
  </div>

<script>
let STATE = { deals: [], alerts: [], health: [], config: {} };

async function loadData() {
  const res = await fetch('/api/state');
  STATE = await res.json();
  renderKpis();
  renderDeals();
  renderAlerts();
  renderHealth();
}

function renderKpis() {
  const inStock = STATE.deals.filter(d => d.in_stock);
  const cheapest = inStock.length ? `€${inStock[0].effective_total_eur.toFixed(2)}` : '—';
  const steals = inStock.filter(d => d.tier === 'STEAL' || d.tier === 'HOT').length;
  const openBox = inStock.filter(d => d.condition !== 'new').length;
  const healthy = STATE.health.filter(h => h.status === 'ok' || h.status === 'partial').length;
  document.getElementById('kpis').innerHTML = `
    <div class="card"><div class="kpi-label">Cheapest In-Stock (SI)</div><div class="kpi-val">${cheapest}</div><div class="small">Target ≤ €${STATE.config.target_price_eur} | Steal ≤ €${STATE.config.steal_price_eur}</div></div>
    <div class="card"><div class="kpi-label">Hot / Steal Deals</div><div class="kpi-val">${steals}</div><div class="small">${inStock.length} in-stock tracked</div></div>
    <div class="card"><div class="kpi-label">Open-Box / B-Ware</div><div class="kpi-val">${openBox}</div><div class="small">Odprta embalaža / Warehouse</div></div>
    <div class="card"><div class="kpi-label">Watchers Healthy</div><div class="kpi-val">${healthy}/${STATE.health.length}</div><div class="small">Auto-refreshes every 30s</div></div>
  `;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderDeals() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const maxP = parseFloat(document.getElementById('maxPrice').value);
  const onlyStock = document.getElementById('onlyStock').checked;
  const onlyOpenBox = document.getElementById('onlyOpenBox').checked;

  const rows = STATE.deals.filter(d => {
    if (onlyStock && !d.in_stock) return false;
    if (onlyOpenBox && d.condition === 'new') return false;
    if (!isNaN(maxP) && d.effective_total_eur > maxP) return false;
    if (q) {
      const hay = `${d.title} ${d.store} ${d.brand} ${d.variant} ${d.discount_summary} ${d.applied_promo_code || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  if (!rows.length) {
    document.getElementById('dealsBody').innerHTML = '<tr><td colspan="7" class="small">No matching deals yet. Run `deal-radar scan` or `deal-radar watch`.</td></tr>';
    return;
  }

  document.getElementById('dealsBody').innerHTML = rows.map(d => `
    <tr class="${d.in_stock ? '' : 'oos'}">
      <td><span class="badge ${esc(d.tier)}">${esc(d.tier)}</span></td>
      <td>
        <strong>€${d.effective_total_eur.toFixed(2)}</strong>
        <div class="small">item €${d.effective_item_price_eur.toFixed(2)} + ship €${d.shipping_to_slovenia_eur.toFixed(2)}</div>
        ${d.original_price_eur ? `<div class="small"><s>€${d.original_price_eur.toFixed(2)}</s></div>` : ''}
      </td>
      <td>
        <a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>
        <div class="small">${esc(d.store)} · ${esc(d.brand)} ${esc(d.variant)} · ATL €${d.all_time_low_eur.toFixed(2)}</div>
      </td>
      <td>
        ${d.condition !== 'new' ? `<span class="badge openbox">${esc(d.condition)}</span>` : 'New'}
      </td>
      <td>
        ${d.applied_promo_code ? `<span class="badge promo">${esc(d.applied_promo_code)}</span> ` : ''}
        <span class="small">${esc(d.discount_summary)}</span>
      </td>
      <td><strong>${d.deal_score}</strong>/100</td>
      <td>${d.in_stock ? '<span class="ok">In Stock</span>' : '<span class="small">Out of Stock</span>'}</td>
    </tr>
  `).join('');
}

function renderAlerts() {
  if (!STATE.alerts.length) {
    document.getElementById('alertsList').innerHTML = 'No alerts triggered yet.';
    return;
  }
  document.getElementById('alertsList').innerHTML = STATE.alerts.map(a => `
    <div style="padding:8px 0;border-bottom:1px solid var(--border)">
      <span class="badge ${esc(a.tier)}">${esc(a.tier)}</span>
      <strong>€${a.effective_total_eur.toFixed(2)}</strong> —
      <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>
      <div class="small">${esc(a.store)} · ${esc(a.discount_summary)} · ${esc(a.created_at)}</div>
    </div>
  `).join('');
}

function renderHealth() {
  if (!STATE.health.length) {
    document.getElementById('healthList').innerHTML = 'No scans recorded yet.';
    return;
  }
  document.getElementById('healthList').innerHTML = STATE.health.map(h => `
    <div style="padding:6px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;gap:8px">
      <div><strong>${esc(h.watcher_name)}</strong> <span class="${esc(h.status)}">[${esc(h.status)}]</span><div class="small">${esc(h.message)}</div></div>
      <div class="small" style="text-align:right">${h.offers_found} offers<br/>${h.duration_ms} ms</div>
    </div>
  `).join('');
}

loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""


def create_dashboard_server(config: RadarConfig, storage: DealStorage) -> ThreadingHTTPServer:
    """Create a local-only HTTP server exposing the Deal Radar dashboard."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return  # Keep terminal clean

        def do_GET(self) -> None:
            if self.path.startswith("/api/state"):
                payload = {
                    "deals": storage.list_best_deals(limit=150, only_in_stock=False),
                    "alerts": storage.list_recent_alerts(limit=30),
                    "health": storage.list_watcher_health(),
                    "config": {
                        "target_price_eur": config.alerts.target_price_eur,
                        "steal_price_eur": config.alerts.steal_price_eur,
                        "reference_market_price_eur": config.alerts.reference_market_price_eur,
                    },
                }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = _HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((config.dashboard.host, config.dashboard.port), Handler)


def start_dashboard_in_background(config: RadarConfig, storage: DealStorage) -> ThreadingHTTPServer:
    server = create_dashboard_server(config, storage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
