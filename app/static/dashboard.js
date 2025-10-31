/**
 * Predictotron Dashboard — dashboard.js
 *
 * Responsibilities:
 *  - Fetch markets from REST API on load and on manual refresh
 *  - Client-side filtering (search + category + source) with debounce
 *  - Render Chart.js line chart for selected market's price history
 *  - Manage WebSocket connection to /ws/markets with exponential-backoff reconnect
 *  - Display live feed of incoming messages, capped at 50 entries
 *  - Update stats bar from /api/v1/markets/stats
 */

'use strict';

/* ─── Constants ─────────────────────────────────────────────── */
const API_BASE   = '/api/v1';
const WS_URL     = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/markets`;
const FEED_MAX   = 50;
const DEBOUNCE_MS = 250;

/* ─── State ──────────────────────────────────────────────────── */
let allMarkets     = [];     // full API response, unfiltered
let filteredMarkets = [];    // currently displayed after filters
let selectedId     = null;   // UUID of selected market
let priceChart     = null;   // Chart.js instance
let ws             = null;   // WebSocket instance
let wsBackoff      = 1000;   // current reconnect delay (ms)
let liveCount      = 0;      // total WS messages received
let feedMessages   = [];     // capped feed buffer
let searchTimeout  = null;   // debounce handle

/* ─── DOM refs ───────────────────────────────────────────────── */
const tbody          = document.getElementById('market-tbody');
const searchInput    = document.getElementById('search');
const catFilter      = document.getElementById('category-filter');
const srcFilter      = document.getElementById('source-filter');
const selectedName   = document.getElementById('selected-name');
const chartEmpty     = document.getElementById('chart-empty');
const chartWrap      = document.getElementById('chart-container-wrap');
const chartMeta      = document.getElementById('chart-meta');
const feedList       = document.getElementById('feed-list');
const feedEmpty      = document.getElementById('feed-empty');
const feedCountEl    = document.getElementById('feed-count');
const wsDot          = document.getElementById('ws-dot');
const wsLabel        = document.getElementById('ws-label');
const statTotal      = document.getElementById('stat-total');
const statActive     = document.getElementById('stat-active');
const statPoints     = document.getElementById('stat-points');
const statLive       = document.getElementById('stat-live');

/* ─── Utilities ──────────────────────────────────────────────── */
function fmt(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtPrice(p) {
  if (p === null || p === undefined) return '—';
  return (parseFloat(p) * 100).toFixed(1) + '%';
}

function categoryBadge(cat) {
  if (!cat) return `<span class="badge badge-default">—</span>`;
  const key = cat.toLowerCase();
  return `<span class="badge badge-${key}">${cat}</span>`;
}

/* ─── Market Fetch & Render ──────────────────────────────────── */
async function loadMarkets() {
  try {
    const res = await fetch(`${API_BASE}/markets?limit=500`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allMarkets = await res.json();
    applyFilters();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:32px">
      Failed to load markets: ${e.message}
    </td></tr>`;
  }
}

async function loadStats() {
  try {
    const res = await fetch(`${API_BASE}/markets/stats`);
    if (!res.ok) return;
    const data = await res.json();
    statTotal.textContent  = fmt(data.total_markets);
    statActive.textContent = fmt(data.active_markets);
    statPoints.textContent = fmt(data.total_price_history_rows);
    statTotal.classList.remove('loading');
    statActive.classList.remove('loading');
    statPoints.classList.remove('loading');
  } catch (_) { /* ignore */ }
}

function applyFilters() {
  const q   = searchInput.value.trim().toLowerCase();
  const cat = catFilter.value;
  const src = srcFilter.value;

  filteredMarkets = allMarkets.filter(m => {
    if (cat && m.category !== cat) return false;
    if (src && m.source !== src)   return false;
    if (q && !m.title.toLowerCase().includes(q)) return false;
    return true;
  });

  renderTable(filteredMarkets);
}

function renderTable(markets) {
  if (markets.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:32px">
      No markets match your filters
    </td></tr>`;
    return;
  }

  const rows = markets.map(m => {
    const isSelected = m.id === selectedId ? 'selected' : '';
    const status = m.resolved
      ? `<span class="status-resolved">Resolved</span>`
      : `<span class="status-active">Active</span>`;
    return `<tr class="${isSelected}" data-id="${m.id}" onclick="selectMarket('${m.id}', this)">
      <td title="${escHtml(m.title)}">${escHtml(truncate(m.title, 60))}</td>
      <td>${categoryBadge(m.category)}</td>
      <td style="color:var(--muted)">${m.source}</td>
      <td style="color:var(--muted)">${fmtDate(m.resolution_date)}</td>
      <td>${status}</td>
    </tr>`;
  });

  tbody.innerHTML = rows.join('');
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function truncate(str, len) {
  return str.length > len ? str.slice(0, len) + '…' : str;
}

/* ─── Market Selection & Chart ───────────────────────────────── */
async function selectMarket(id, rowEl) {
  // Deselect previous
  document.querySelectorAll('tbody tr.selected').forEach(r => r.classList.remove('selected'));
  rowEl.classList.add('selected');
  selectedId = id;

  const market = allMarkets.find(m => m.id === id);
  selectedName.textContent = market ? market.title : id;

  // Show loading state
  chartEmpty.style.display = 'none';
  chartWrap.style.display = 'none';
  chartMeta.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/markets/${id}/price-history?limit=500`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const history = await res.json();

    if (!history || history.length === 0) {
      chartEmpty.textContent = 'No price history available for this market';
      chartEmpty.style.display = 'flex';
      return;
    }

    renderChart(history);
  } catch (e) {
    chartEmpty.textContent = `Failed to load chart: ${e.message}`;
    chartEmpty.style.display = 'flex';
  }
}

function renderChart(history) {
  const labels = history.map(p => {
    const d = new Date(p.timestamp);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  });

  const prices  = history.map(p => (parseFloat(p.price) * 100).toFixed(2));
  const latest  = parseFloat(history[history.length - 1].price);
  const earliest = parseFloat(history[0].price);
  const change  = latest - earliest;
  const changeSign = change >= 0 ? '+' : '';
  const trend   = change >= 0.01 ? 'up' : change <= -0.01 ? 'down' : 'neutral';

  const high = Math.max(...history.map(p => parseFloat(p.price)));
  const low  = Math.min(...history.map(p => parseFloat(p.price)));
  const vol  = history[history.length - 1].volume_24h;

  // Build gradient
  const canvas = document.getElementById('price-chart');
  const ctx    = canvas.getContext('2d');

  const accentColor = trend === 'up' ? '#22c55e' : trend === 'down' ? '#ef4444' : '#6366f1';
  const grad = ctx.createLinearGradient(0, 0, 0, 160);
  grad.addColorStop(0, hexToRgba(accentColor, 0.3));
  grad.addColorStop(1, hexToRgba(accentColor, 0.0));

  if (priceChart) { priceChart.destroy(); priceChart = null; }

  priceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Implied Probability (%)',
        data: prices,
        borderColor: accentColor,
        backgroundColor: grad,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: accentColor,
        tension: 0.3,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1d27',
          borderColor: '#2d3150',
          borderWidth: 1,
          titleColor: '#94a3b8',
          bodyColor: '#e2e8f0',
          padding: 10,
          callbacks: {
            label: ctx => ` ${parseFloat(ctx.parsed.y).toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(45,49,80,0.5)', drawBorder: false },
          ticks: {
            color: '#475569',
            maxTicksLimit: 8,
            font: { size: 10 },
          },
        },
        y: {
          grid: { color: 'rgba(45,49,80,0.5)', drawBorder: false },
          ticks: {
            color: '#475569',
            font: { size: 10 },
            callback: v => v + '%',
          },
        },
      },
    },
  });

  // Meta row
  chartMeta.innerHTML = `
    <div class="meta-item">
      <div class="meta-label">Current</div>
      <div class="meta-value ${trend}">${fmtPrice(latest)}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Change</div>
      <div class="meta-value ${trend}">${changeSign}${(change * 100).toFixed(1)}pp</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">High</div>
      <div class="meta-value neutral">${fmtPrice(high)}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Low</div>
      <div class="meta-value neutral">${fmtPrice(low)}</div>
    </div>
    ${vol ? `<div class="meta-item">
      <div class="meta-label">Volume</div>
      <div class="meta-value neutral">$${fmt(Math.round(vol))}</div>
    </div>` : ''}
  `;

  chartEmpty.style.display = 'none';
  chartWrap.style.display  = 'block';
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* ─── WebSocket ──────────────────────────────────────────────── */
function connectWS() {
  setWsStatus('connecting');

  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.addEventListener('open', () => {
    wsBackoff = 1000;
    setWsStatus('connected');
  });

  ws.addEventListener('message', event => {
    let data;
    try { data = JSON.parse(event.data); }
    catch { return; }
    handleWsMessage(data);
  });

  ws.addEventListener('close', () => {
    setWsStatus('disconnected');
    scheduleReconnect();
  });

  ws.addEventListener('error', () => {
    ws.close();
  });
}

function scheduleReconnect() {
  setWsStatus('connecting');
  setTimeout(() => connectWS(), wsBackoff);
  wsBackoff = Math.min(wsBackoff * 2, 30_000); // cap at 30s
}

function setWsStatus(state) {
  wsDot.className = 'dot ' + state;
  wsLabel.textContent = {
    connected:    'Live',
    connecting:   'Connecting…',
    disconnected: 'Disconnected',
  }[state] ?? state;
}

function handleWsMessage(data) {
  liveCount++;
  statLive.textContent = fmt(liveCount);

  // If this message is a price update for the selected market, update chart
  if (
    data.type === 'price_update' &&
    data.market_id === selectedId &&
    priceChart
  ) {
    const newPrice = (parseFloat(data.price) * 100).toFixed(2);
    const label    = fmtDate(data.timestamp);
    priceChart.data.labels.push(label);
    priceChart.data.datasets[0].data.push(newPrice);
    // Keep chart at max 500 points
    if (priceChart.data.labels.length > 500) {
      priceChart.data.labels.shift();
      priceChart.data.datasets[0].data.shift();
    }
    priceChart.update('none');
  }

  addFeedMessage(data);
}

function addFeedMessage(data) {
  feedMessages.unshift(data);
  if (feedMessages.length > FEED_MAX) feedMessages.pop();

  feedCountEl.textContent = `${feedMessages.length} message${feedMessages.length !== 1 ? 's' : ''}`;

  // Hide empty state
  if (feedEmpty) feedEmpty.style.display = 'none';

  // Build feed item
  const item = document.createElement('div');
  item.className = 'feed-item';

  const type = data.type ?? 'update';
  const time = data.timestamp ? fmtTime(data.timestamp) : fmtTime(new Date().toISOString());

  let priceHtml = '';
  if (data.price !== undefined) {
    const pct   = (parseFloat(data.price) * 100).toFixed(1);
    const dir   = data.prev_price !== undefined
      ? (data.price > data.prev_price ? 'up' : data.price < data.prev_price ? 'down' : 'neutral')
      : 'neutral';
    priceHtml = `<span class="feed-price ${dir}">${pct}%</span>`;
  }

  const mid = data.market_id
    ? `<span style="color:var(--muted)">${data.market_id.slice(0, 8)}…</span>`
    : '';

  item.innerHTML = `
    <div class="feed-item-top">
      <span class="feed-type">${escHtml(type.replace(/_/g, ' '))}</span>
      <span class="feed-time">${time}</span>
    </div>
    <div class="feed-msg">${mid} ${priceHtml}</div>
  `;

  // Prepend to keep newest at top
  const firstChild = feedList.firstChild;
  if (firstChild && firstChild.id !== 'feed-empty') {
    feedList.insertBefore(item, firstChild);
  } else {
    feedList.appendChild(item);
  }

  // Trim excess DOM nodes
  while (feedList.children.length > FEED_MAX + 1) {
    feedList.removeChild(feedList.lastChild);
  }
}

/* ─── Filter Event Listeners ─────────────────────────────────── */
searchInput.addEventListener('input', () => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(applyFilters, DEBOUNCE_MS);
});

catFilter.addEventListener('change', applyFilters);
srcFilter.addEventListener('change', applyFilters);

/* ─── Init ───────────────────────────────────────────────────── */
(async function init() {
  await Promise.all([loadMarkets(), loadStats()]);
  connectWS();
  // Refresh stats every 30s
  setInterval(loadStats, 30_000);
})();
