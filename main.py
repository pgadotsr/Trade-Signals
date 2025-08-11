# main.py
import os
import time
import threading
import math
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, jsonify
import pandas as pd
import oandapyV20
import oandapyV20.endpoints.instruments as instruments

# ----------------------------
# Configuration / Environment
# ----------------------------
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENV = os.getenv("OANDA_ENV", "practice").lower()  # "practice" or "live"

if not OANDA_API_KEY:
    print("Warning: OANDA_API_KEY not set. API calls will fail until you add the key to environment variables.")

OANDA_URL = {
    "practice": "https://api-fxpractice.oanda.com/v3",
    "live": "https://api-fxtrade.oanda.com/v3"
}.get(OANDA_ENV, "https://api-fxpractice.oanda.com/v3")

# Instruments order: Gold first
INSTRUMENTS = [
    "XAU_USD",     # Gold
    "NAS100_USD",  # US Tech 100
    "US30_USD",    # USA 30
    "DE40_EUR",    # Germany 40
    "UK100_GBP",   # UK 100
    "EU50_EUR",    # EU 50
    "GBP_USD",
    "EUR_USD",
    "USD_JPY",
    "AUD_USD"
]

# Candles to fetch per timeframe
CANDLES_COUNT = 100

# Refresh interval (background fetch)
REFRESH_INTERVAL = 5  # seconds

# ----------------------------
# App and OANDA client
# ----------------------------
app = Flask(__name__)
api = oandapyV20.API(access_token=OANDA_API_KEY, environment="practice" if OANDA_ENV == "practice" else "live")

# In-memory data store (updated by background thread)
data_store = {sym: {} for sym in INSTRUMENTS}
store_lock = threading.Lock()

# ----------------------------
# Util: fetch candles from OANDA
# ----------------------------
def fetch_candles_oanda(instrument, granularity="M15", count=CANDLES_COUNT):
    """
    Returns a dataframe with columns time, open, high, low, close
    If error, returns empty dataframe.
    """
    params = {"granularity": granularity, "count": count, "price": "M"}
    try:
        req = instruments.InstrumentsCandles(instrument=instrument, params=params)
        api.request(req)
        candles = req.response.get("candles", [])
        rows = []
        for c in candles:
            if not c.get("complete", True) and c.get("volume", 0) == 0:
                # skip incomplete maybe
                pass
            mid = c.get("mid") or c.get("mid")
            if not mid:
                continue
            rows.append({
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"])
            })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # convert time to pd datetime if needed
        df["time"] = pd.to_datetime(df["time"])
        return df
    except Exception as e:
        print(f"[fetch_candles_oanda] error for {instrument} {granularity}: {e}")
        return pd.DataFrame()

# ----------------------------
# Signal logic
# ----------------------------
def sma(series, window):
    if len(series) < window:
        return None
    return series.rolling(window=window).mean()

def compute_primary_bias(df_h1):
    """Primary bias uses H1 MA50: returns 'UP', 'DOWN', or None"""
    if df_h1 is None or df_h1.empty:
        return None
    closes = df_h1["close"]
    if len(closes) < 50:
        return None
    ma50 = closes.rolling(window=50).mean().iloc[-1]
    last = closes.iloc[-1]
    return "UP" if last > ma50 else "DOWN"

def check_timeframe_signal(df, min_points):
    """
    Basic rule: check if there is spare room for min_points on the upside (BUY) or downside (SELL)
    Uses recent high/low vs last close.
    Returns dict: {"side": "BUY"|"SELL"|None, "entry": float, "tp": float, "sl": float}
    """
    if df is None or df.empty:
        return None
    last = df["close"].iloc[-1]
    recent_high = df["high"].max()
    recent_low = df["low"].min()
    # Determine directional candidates
    if (recent_high - last) >= min_points:
        entry = last
        tp = round(entry + min_points, 6)
        sl = round(entry - (min_points * 0.5), 6)  # example SL half the TP distance
        return {"side": "BUY", "entry": round(entry, 6), "tp": tp, "sl": sl}
    elif (last - recent_low) >= min_points:
        entry = last
        tp = round(entry - min_points, 6)
        sl = round(entry + (min_points * 0.5), 6)
        return {"side": "SELL", "entry": round(entry, 6), "tp": tp, "sl": sl}
    else:
        return None

def aggregate_signals(symbol, df_h1, df_15m, df_5m, df_1m, min_points):
    """
    Require:
      - 15m and 5m agree on side (buy/sell)
      - 1m confirms same side (if available)
      - Primary bias (H1 MA50) is used as tie-breaker (if available)
    Returns trade candidate dict or None.
    """
    s15 = check_timeframe_signal(df_15m, min_points)
    s5 = check_timeframe_signal(df_5m, min_points)
    s1 = check_timeframe_signal(df_1m, min_points)

    if not s15 or not s5:
        return None
    # Both must have sides and agree
    side15 = s15["side"]
    side5 = s5["side"]
    if not side15 or not side5:
        return None
    if side15 != side5:
        return None

    # 1m confirmation if exists
    if s1 and s1["side"] != side15:
        return None

    # Primary bias
    primary = compute_primary_bias(df_h1)
    # if primary exists and contradicts, reject (user wanted H1 bias)
    if primary:
        if (primary == "UP" and side15 == "SELL") or (primary == "DOWN" and side15 == "BUY"):
            return None

    # Good candidate: use the more conservative of (s15 and s5) entry (we'll pick average)
    entry = round((s15["entry"] + s5["entry"]) / 2.0, 6)
    # TP: if both provide TP, choose the one with further TP in direction of trade
    if side15 == "BUY":
        tp = max(s15["tp"], s5["tp"])
        sl = min(s15["sl"], s5["sl"])
    else:
        tp = min(s15["tp"], s5["tp"])
        sl = max(s15["sl"], s5["sl"])

    return {
        "side": side15,
        "entry": entry,
        "tp": round(tp, 6),
        "sl": round(sl, 6),
        "primary_bias": primary
    }

# ----------------------------
# Background updater thread
# ----------------------------
def update_loop():
    """Periodically fetch multiple timeframes for all instruments and compute signals"""
    while True:
        for sym in INSTRUMENTS:
            # fetch candles
            df_h1 = fetch_candles_oanda(sym, "H1", 100)
            df_15m = fetch_candles_oanda(sym, "M15", 100)
            df_5m = fetch_candles_oanda(sym, "M5", 100)
            df_1m = fetch_candles_oanda(sym, "M1", 100)

            # compute primary bias
            primary = compute_primary_bias(df_h1)

            # compute candidates for +10 and +5 rules
            cand_10 = aggregate_signals(sym, df_h1, df_15m, df_5m, df_1m, min_points=10)
            cand_5 = aggregate_signals(sym, df_h1, df_15m, df_5m, df_1m, min_points=5)

            # store results thread-safely
            with store_lock:
                data_store[sym] = {
                    "updated_at": datetime.utcnow().isoformat(),
                    "primary_bias": primary,
                    "cand_10": cand_10,
                    "cand_5": cand_5,
                    "h1": df_h1.tail(200).to_dict(orient="records") if not df_h1.empty else [],
                    "m15": df_15m.tail(200).to_dict(orient="records") if not df_15m.empty else [],
                    "m5": df_5m.tail(200).to_dict(orient="records") if not df_5m.empty else [],
                    "m1": df_1m.tail(200).to_dict(orient="records") if not df_1m.empty else []
                }
        time.sleep(REFRESH_INTERVAL)

# Start background thread
thread = threading.Thread(target=update_loop, daemon=True)
thread.start()

# ----------------------------
# API endpoint to return JSON for selected symbol
# ----------------------------
@app.route("/api/symbol")
def api_symbol():
    sym = request.args.get("symbol", INSTRUMENTS[0])
    with store_lock:
        payload = data_store.get(sym, {})
    return jsonify(payload)

@app.route("/api/status")
def api_status():
    # quick health/status: list symbols and whether trade available (cand_10)
    out = {}
    with store_lock:
        for s in INSTRUMENTS:
            d = data_store.get(s, {})
            out[s] = {
                "cand_10": bool(d.get("cand_10")),
                "cand_5": bool(d.get("cand_5"))
            }
    return jsonify(out)

# ----------------------------
# HTML template (serves whole UI)
# ----------------------------
TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Trade Signals — Combined 1m/5m/15m</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- Chart.js CDN -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial; background:#fff; color:#111; padding:12px; }
    header { text-align:center; margin-bottom:8px; }
    .controls { margin-bottom:12px; }
    select { padding:8px; font-size:16px; }
    .status-dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:8px; vertical-align:middle; }
    .green { background: #0a0; }
    .red { background: #d00; }
    .panel { background:#f6f6f6; padding:12px; border-radius:8px; margin-bottom:14px; }
    .chart-wrap { width:100%; max-width:920px; margin:auto; }
    table { width:100%; border-collapse:collapse; margin-top:8px; }
    td, th { padding:10px; border:1px solid #ddd; text-align:left; }
    .buy { color: #0a0; font-weight:700; }
    .sell { color: #d00; font-weight:700; }
    .no-trade { color: #777; }
    .small { font-size:12px; color:#666; }
  </style>
</head>
<body>
  <header>
    <h1>Signals — Combined 1m / 5m / 15m</h1>
    <div class="small">Primary bias = 1h MA50. Signals require 15m & 5m agreement to propose a trade; 1m is confirmation. Auto-updates every 5s.</div>
  </header>

  <div class="controls panel">
    Asset:
    <select id="assetSelect"></select>
    <span id="lastUpdated" style="margin-left:12px;" class="small"></span>
  </div>

  <div id="chartsArea" class="panel">
    <div class="chart-wrap">
      <canvas id="chart10" height="160"></canvas>
    </div>
    <div id="table10"></div>
  </div>

  <div id="chartsArea5" class="panel">
    <div class="chart-wrap">
      <canvas id="chart5" height="160"></canvas>
    </div>
    <div id="table5"></div>
  </div>

<script>
const instruments = {{ instruments|tojson }};
let current = instruments[0];
const REFRESH_MS = 5000; // 5 seconds

// populate dropdown and color it
async function refreshStatusDots() {
  const statusResp = await fetch('/api/status');
  const status = await statusResp.json();
  const sel = document.getElementById('assetSelect');
  sel.innerHTML = '';
  instruments.forEach(sym => {
    const opt = document.createElement('option');
    opt.value = sym;
    opt.text = sym;
    // color: if cand_10 exists -> green, else red if cand_5 exists -> orange else grey
    const has10 = status[sym] && status[sym].cand_10;
    const has5 = status[sym] && status[sym].cand_5;
    if (has10) {
      opt.style.color = 'green';
    } else if (has5) {
      opt.style.color = 'orange';
    } else {
      opt.style.color = 'grey';
    }
    sel.appendChild(opt);
  });
  // maintain selection
  sel.value = current;
  sel.onchange = (e) => {
    current = e.target.value;
    loadSymbol(current, true); // immediate load
  }
}

// chart instances
let chart10 = null;
let chart5 = null;

function createEmptyChart(ctx) {
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Price', data: [], borderWidth: 1, pointRadius: 0 }] },
    options: {
      animation: false,
      scales: {
        x: { display: true },
        y: { display: true }
      },
      plugins: { legend: { display: false } }
    }
  });
}

window.addEventListener('load', async () => {
  await refreshStatusDots();
  const c10 = document.getElementById('chart10').getContext('2d');
  const c5 = document.getElementById('chart5').getContext('2d');
  chart10 = createEmptyChart(c10);
  chart5 = createEmptyChart(c5);
  loadSymbol(current, true);
  setInterval(periodicUpdate, REFRESH_MS);
});

async function periodicUpdate() {
  await refreshStatusDots();
  loadSymbol(current, false); // no forced immediate visualshift, but updates
}

function buildTableHtml(candidate, minPoints) {
  if (!candidate) {
    return '<div class="no-trade">No trade</div>';
  }
  const cls = candidate.side === 'BUY' ? 'buy' : 'sell';
  const html = `
    <table>
      <tr><th>TF</th><th>Direction</th><th>Entry</th><th>Take Profit</th><th>Stop Loss</th></tr>
      <tr><td>1m</td><td class="${cls}">${candidate.side}</td><td>${candidate.entry}</td><td>${candidate.tp}</td><td>${candidate.sl}</td></tr>
      <tr><td>5m</td><td class="${cls}">${candidate.side}</td><td>${candidate.entry}</td><td>${candidate.tp}</td><td>${candidate.sl}</td></tr>
      <tr><td>15m</td><td class="${cls}">${candidate.side}</td><td>${candidate.entry}</td><td>${candidate.tp}</td><td>${candidate.sl}</td></tr>
    </table>
  `;
  return html;
}

async function loadSymbol(sym, forceScroll=false) {
  try {
    const resp = await fetch('/api/symbol?symbol=' + encodeURIComponent(sym));
    const data = await resp.json();
    const now = new Date();
    document.getElementById('lastUpdated').innerText = 'Last updated: ' + (data.updated_at || now.toISOString());
    // update +10 chart/table
    const m15 = data.m15 || [];
    const m5 = data.m5 || [];
    // build labels and prices from m15 (for charting context)
    const labels = m15.map(r => new Date(r.time).toLocaleTimeString());
    const prices = m15.map(r => r.close);
    // update chart10 dataset
    chart10.data.labels = labels;
    chart10.data.datasets[0].data = prices;
    chart10.update('none');

    // +10 table
    const candidate10 = data.cand_10 || null;
    document.getElementById('table10').innerHTML = '<h3>+10 Point Rule</h3>' + buildTableHtml(candidate10, 10);

    // +5 chart: display m5
    const labels5 = m5.map(r => new Date(r.time).toLocaleTimeString());
    const prices5 = m5.map(r => r.close);
    chart5.data.labels = labels5;
    chart5.data.datasets[0].data = prices5;
    chart5.update('none');

    const candidate5 = data.cand_5 || null;
    document.getElementById('table5').innerHTML = '<h3>+5 Point Rule</h3>' + buildTableHtml(candidate5, 5);

    // highlight dropdown color (refreshed in refreshStatusDots but keep selection color)
    document.getElementById('assetSelect').value = sym;

    // if forceScroll true: scroll to top of page so user sees charts
    if (forceScroll) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  } catch (err) {
    console.error("loadSymbol error:", err);
  }
}
</script>
</body>
</html>
"""

# ----------------------------
# Front page
# ----------------------------
@app.route("/")
def index():
    return render_template_string(TEMPLATE, instruments=INSTRUMENTS)

# ----------------------------
# Run (dev) or production via gunicorn
# ----------------------------
if __name__ == "__main__":
    # Local dev testing
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False, threaded=True)
