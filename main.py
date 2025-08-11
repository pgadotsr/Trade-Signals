# main.py
# OANDA live multi-timeframe signals with +10 and +5 rules, charts, dropdown coloring
# Requirements: flask, requests, pandas, numpy, ta, gunicorn
# Start command: gunicorn main:app

import os
import time
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify
import requests
import pandas as pd
import numpy as np
import ta

app = Flask(__name__)

# ------------------ CONFIG ------------------
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")  # practice or live
OANDA_BASE = "https://api-fxpractice.oanda.com/v3" if OANDA_ENV == "practice" else "https://api-fxtrade.oanda.com/v3"
HEADERS = {"Authorization": f"Bearer {OANDA_API_KEY}"}

# NOTE: OANDA instrument names can vary by account/provider.
# If any instrument returns N/A, update the mapping to the exact name your OANDA account uses.
ASSETS = {
    "Gold (XAU/USD)"      : "XAU_USD",
    "USA Tech 100"        : "NAS100_USD",   # may need adjustment in your OANDA account
    "USA 30"              : "US30_USD",     # may need adjustment
    "Germany 40"          : "GER40_EUR",    # may need adjustment
    "UK 100"              : "UK100_GBP",    # may need adjustment
    "EU 50"               : "EU50_EUR",     # may need adjustment
    "GBP/USD"             : "GBP_USD",
    "EUR/USD"             : "EUR_USD",
    "USD/JPY"             : "USD_JPY",
    "USD/CHF"             : "USD_CHF",
    "AUD/USD"             : "AUD_USD",
    "NZD/USD"             : "NZD_USD"
}

# Minimum TP distances in instrument price units (tunable)
MIN_TP = {
    "XAU_USD": 10.0,
    "NAS100_USD": 10.0,
    "US30_USD": 10.0,
    "GER40_EUR": 5.0,
    "UK100_GBP": 5.0,
    "EU50_EUR": 5.0,
    "GBP_USD": 0.0020,
    "EUR_USD": 0.0020,
    "USD_JPY": 0.15,
    "USD_CHF": 0.0020,
    "AUD_USD": 0.0020,
    "NZD_USD": 0.0020
}

CACHE = {}
CACHE_TTL = 20  # seconds
# ---------------------------------------------

# ---------- caching helpers ----------
def cache_get(key):
    rec = CACHE.get(key)
    if rec and time.time() - rec["ts"] < CACHE_TTL:
        return rec["val"]
    return None

def cache_set(key, val):
    CACHE[key] = {"val": val, "ts": time.time()}
# -------------------------------------

# ---------- OANDA candles ----------
def fetch_oanda_candles(instrument, granularity="M15", count=200):
    """Return DataFrame oldest->newest of completed candles or None."""
    key = f"{instrument}:{granularity}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    url = f"{OANDA_BASE}/instruments/{instrument}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=12)
        r.raise_for_status()
        j = r.json()
        candles = j.get("candles")
        if not candles:
            cache_set(key, None)
            return None
        rows = []
        for c in candles:
            if not c.get("complete", False):
                continue
            mid = c.get("mid", {})
            rows.append({
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"])
            })
        df = pd.DataFrame(rows)
        if df.empty:
            cache_set(key, None)
            return None
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").sort_index()
        cache_set(key, df)
        return df
    except Exception:
        cache_set(key, None)
        return None
# -----------------------------------

# ---------- Indicators & utilities ----------
def atr(df, period=14):
    try:
        a = ta.volatility.AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=period).average_true_range()
        return float(a.iloc[-1])
    except Exception:
        # fallback simple measure
        closes = df["close"].values
        if len(closes) < 2: return None
        diffs = np.abs(np.diff(closes))
        return float(np.mean(diffs[-period:])) if len(diffs)>0 else None

def ema_dir(df, fast=9, slow=21):
    if df is None or len(df) < slow+2: return None
    try:
        ef = ta.trend.ema_indicator(df["close"], window=fast).iloc[-1]
        es = ta.trend.ema_indicator(df["close"], window=slow).iloc[-1]
        return "BUY" if ef > es else "SELL"
    except Exception:
        return None

def one_min_confirm(df1m):
    if df1m is None or len(df1m) < 2: return None
    last = df1m["close"].iloc[-1]
    prev = df1m["close"].iloc[-2]
    return "Up" if last > prev else "Down" if last < prev else "Flat"

def swings_24h(df_15m):
    if df_15m is None or len(df_15m) < 6: return (None, None)
    window = min(len(df_15m), 96)  # approx 24h
    recent = df_15m.tail(window)
    return (float(recent["high"].max()), float(recent["low"].min()))

def recent_30m_range(df_1m):
    if df_1m is None or len(df_1m) < 10: return 0.0
    recent = df_1m.tail(30)
    return float(recent["high"].max() - recent["low"].min())
# ----------------------------------------

# ---------- compute TP/SL with min + ATR + swing ----------
def compute_tp_sl(entry, direction, df_15m, instrument, min_tp_base):
    """
    entry: float price
    direction: "BUY" or "SELL"
    df_15m: 15m DF for ATR and swing
    min_tp_base: minimum TP distance (10 or 5)
    returns: (tp, sl, reason_flag) where reason_flag=="ok" or reason string
    """
    if entry is None:
        return (None, None, "no_entry")

    atr15 = atr(df_15m)
    atr_val = atr15 if atr15 and atr15>0 else 0.0

    # base distance = min_tp_base + ATR(15m)
    base_distance = max(min_tp_base, min_tp_base + atr_val)  # ensures at least min_tp_base

    # attempt swing-based extension (prefer larger)
    swing_high, swing_low = swings_24h(df_15m)
    buffer = max(abs(entry)*0.002, 1.0)  # buffer to avoid hitting exact swing (changeable)

    swing_tp = None
    if direction == "BUY":
        if swing_high is not None:
            cand = swing_high - buffer
            dist = cand - entry
            if dist >= base_distance:
                swing_tp = cand
    elif direction == "SELL":
        if swing_low is not None:
            cand = swing_low + buffer
            dist = entry - cand
            if dist >= base_distance:
                swing_tp = cand

    if swing_tp is not None:
        tp = swing_tp
    else:
        tp = entry + base_distance if direction=="BUY" else entry - base_distance

    # SL: ATR * 1.0 (keeps risk scaled)
    sl_dist = atr_val if atr_val>0 else base_distance/2.0
    sl = entry - sl_dist if direction=="BUY" else entry + sl_dist

    final_dist = (tp - entry) if direction=="BUY" else (entry - tp)
    if final_dist < min_tp_base:
        return (None, None, "tp_too_small")

    return (round(float(tp),6), round(float(sl),6), "ok")

# ----------------------------------------

# ---------- full analysis per instrument ----------
def analyze_instrument(oanda_symbol):
    """Return analysis dict used by UI and charts for both rules."""
    df_1h = fetch_oanda_candles(oanda_symbol, "H1", count=200)
    df_15 = fetch_oanda_candles(oanda_symbol, "M15", count=200)
    df_5  = fetch_oanda_candles(oanda_symbol, "M5", count=200)
    df_1  = fetch_oanda_candles(oanda_symbol, "M1", count=200)

    out = {"ok": True, "reason": None, "primary_bias":"UNKNOWN", "ma50":None, "data":{}}

    # primary bias from MA50 on 1h
    try:
        if df_1h is not None and len(df_1h)>=60:
            ma50 = df_1h["close"].rolling(window=50).mean().iloc[-1]
            cur = float(df_1h["close"].iloc[-1])
            out["ma50"]=round(float(ma50),6)
            diff_pct = (cur - ma50)/ma50*100
            out["primary_bias"] = "UP" if diff_pct>0.5 else "DOWN" if diff_pct<-0.5 else "SIDEWAYS"
    except Exception:
        out["primary_bias"]="UNKNOWN"

    # compute directions
    dir_15 = ema_dir(df_15)
    dir_5  = ema_dir(df_5)
    dir_1  = ema_dir(df_1)
    confirm_1m = one_min_confirm(df_1)

    # make results for both rules (+10 and +5)
    entry = float(df_1["close"].iloc[-1]) if df_1 is not None and len(df_1)>0 else None

    # decide agreement: 15m & 5m must match and be BUY/SELL
    agreement = (dir_15 in ("BUY","SELL") and dir_5 in ("BUY","SELL") and dir_15==dir_5)
    agreement_side = dir_15 if agreement else None

    # compute recent 30m range and ensure it's big enough for min TP thresholds
    range_30m = recent_30m_range(df_1)

    # rules handling
    results = {}
    for min_tp in (10.0, 5.0):  # first 10-rule, then 5-rule
        rule_name = f"min_{int(min_tp)}"
        info = {"signal":"NO TRADE","entry":None,"tp":None,"sl":None,"reason":"unknown","dir_15":dir_15,"dir_5":dir_5,"dir_1":dir_1,"confirm_1m":confirm_1m}
        # quick checks
        if not agreement:
            info["reason"]="15m and 5m do not agree"
            results[rule_name]=info
            continue
        if not (dir_1 in ("BUY","SELL")):
            info["reason"]="1m EMA direction unknown"
            results[rule_name]=info
            continue
        # require 1m confirmation to match agreed side
        if dir_1 != agreement_side:
            info["reason"]="1m EMA doesn't match 5m/15m agreement"
            results[rule_name]=info
            continue
        # check 30m volatility supports min TP (use price-units)
        min_tp_required = min_tp
        # convert pip-like for FX? MIN_TP mapping adapted earlier for instrument scale. We'll use absolute min_tp here,
        # but we also check MIN_TP mapping to ensure reasonable for instrument
        mapped_min = MIN_TP.get(oanda_symbol, min_tp_required)
        if range_30m < mapped_min:
            info["reason"] = f"Low 30m volatility ({range_30m:.4f}) < required {mapped_min}"
            results[rule_name]=info
            continue
        # compute TP/SL using ATR(15m) + swing
        tp, sl, flag = compute_tp_sl(entry, agreement_side, df_15, oanda_symbol, mapped_min)
        if flag != "ok":
            info["reason"] = flag
            results[rule_name]=info
            continue
        # success
        info.update({"signal":agreement_side,"entry":round(entry,6),"tp":tp,"sl":sl,"reason":"ok"})
        results[rule_name]=info

    # prepare chart series: recent closes from 1m for plotting
    chart_series = []
    if df_1 is not None and len(df_1)>0:
        chart_series = [{"time": str(idx), "close": float(v)} for idx,v in zip(df_1.index.astype(str), df_1["close"].values)]

    out["data"] = {"results": results, "chart": chart_series, "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
    return out

# ----------------------------------------

# ---------- API endpoint used by page ----------
@app.route("/api/asset", methods=["GET"])
def api_asset():
    name = request.args.get("asset")
    if not name or name not in ASSETS:
        return jsonify({"error":"unknown asset"}), 400
    sym = ASSETS[name]
    analysis = analyze_instrument(sym)
    return jsonify(analysis)

# ---------- Utility to evaluate whole menu coloring ----------
def evaluate_all_assets():
    menu = {}
    for name,sym in ASSETS.items():
        a = analyze_instrument(sym)
        # green if either min_10 or min_5 has reason "ok"
        r10 = a["data"]["results"].get("min_10",{})
        r5  = a["data"]["results"].get("min_5",{})
        ok = (r10.get("reason")=="ok") or (r5.get("reason")=="ok")
        menu[name] = {"trade_available": ok}
    return menu

# ---------- Frontend HTML ----------
PAGE_HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Signals — +10 / +5</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body{font-family:Arial;margin:12px;color:#111}
    header{display:flex;justify-content:space-between;align-items:center}
    select{padding:8px;font-size:16px}
    .green{color:green;font-weight:bold}
    .red{color:red;font-weight:bold}
    .container{max-width:1100px;margin:10px auto}
    .card{background:#f7f7f7;padding:12px;border-radius:8px;margin-bottom:14px}
    canvas{width:100%;height:260px}
    table{width:100%;border-collapse:collapse}
    th,td{padding:8px;border:1px solid #ddd;text-align:center}
    th{background:#eee}
    .buy{color:green;font-weight:bold}
    .sell{color:red;font-weight:bold}
    .not{color:#888}
  </style>
</head>
<body>
  <header>
    <h2>Live Signals (OANDA)</h2>
    <div>
      Asset:
      <select id="assetSelect"></select>
    </div>
  </header>

  <div class="container">
    <div id="menuNote" class="small"></div>

    <div class="card" id="topSection">
      <h3>Priority trades — +10 rule</h3>
      <div id="topInfo" class="small"></div>
      <canvas id="chart10"></canvas>
      <table id="table10">
        <thead><tr><th>TF</th><th>Direction</th><th>Entry</th><th>TP</th><th>SL</th><th>1m Confirm</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>

    <div class="card" id="botSection">
      <h3>Secondary trades — +5 rule</h3>
      <div id="botInfo" class="small"></div>
      <canvas id="chart5"></canvas>
      <table id="table5">
        <thead><tr><th>TF</th><th>Direction</th><th>Entry</th><th>TP</th><th>SL</th><th>1m Confirm</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>

    <div class="small">Primary bias = 1h MA50. 15m & 5m must agree; 1m confirmation required. Auto-refresh every 60s.</div>
    <div class="small" id="updatedAt"></div>
  </div>

<script>
const ASSETS = {{ assets|tojson }};
const CACHE_TTL = {{ cache_ttl }};
let chart10=null, chart5=null;

function buildMenu(menuStatus){
  const sel = document.getElementById('assetSelect');
  sel.innerHTML = '';
  for (const name of Object.keys(ASSETS)){
    const option = document.createElement('option');
    option.value = name;
    option.text = name;
    const has = menuStatus[name] && menuStatus[name].trade_available;
    option.style.color = has ? 'green' : 'red';
    sel.appendChild(option);
  }
}

async function loadMenuStatus(){
  // call endpoint to evaluate all assets (could be heavy; cached)
  const resp = await fetch('/api/menu_status');
  const j = await resp.json();
  buildMenu(j);
}

function formatRow(tf, d){
  return `<tr class="${d.direction=='BUY'?'buy':d.direction=='SELL'?'sell':'not'}">
    <td>${tf}</td>
    <td>${d.direction}</td>
    <td>${d.entry||'N/A'}</td>
    <td>${d.tp||'N/A'}</td>
    <td>${d.sl||'N/A'}</td>
    <td>${d.confirm||'N/A'}</td>
  </tr>`;
}

function updateTable(sectionId, result){
  const tbody = document.querySelector(`#${sectionId} tbody`);
  tbody.innerHTML = '';
  // rows: 1m,5m,15m
  const rows = [
    {tf:'1m', data: {direction: result.dir_1, entry: result.entry, tp: result.tp, sl: result.sl, confirm: result.confirm_1m}},
    {tf:'5m', data: {direction: result.dir_5, entry: result.entry, tp: result.tp, sl: result.sl, confirm: result.confirm_1m}},
    {tf:'15m', data: {direction: result.dir_15, entry: result.entry, tp: result.tp, sl: result.sl, confirm: result.confirm_1m}}
  ];
  for (const r of rows){
    tbody.innerHTML += `<tr class="${r.data.direction=='BUY'?'buy':r.data.direction=='SELL'?'sell':'not'}">
      <td><strong>${r.tf}</strong></td>
      <td>${r.data.direction||'N/A'}</td>
      <td>${r.data.entry||'N/A'}</td>
      <td>${r.data.tp||'N/A'}</td>
      <td>${r.data.sl||'N/A'}</td>
      <td>${r.data.confirm||'N/A'}</td>
    </tr>`;
  }
}

function buildChart(canvasId, series, tp=null, sl=null){
  const ctx = document.getElementById(canvasId).getContext('2d');
  const labels = series.map(p => new Date(p.time).toLocaleTimeString());
  const data = series.map(p => p.close);
  if (canvasId==='chart10' && chart10){ chart10.destroy(); chart10=null }
  if (canvasId==='chart5' && chart5){ chart5.destroy(); chart5=null }
  const config = {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Price',
        data,
        fill: false,
        borderColor: '#007bff',
        tension: 0.1,
        pointRadius: 0
      }]
    },
    options: {
      animation:false,
      plugins:{
        legend:{display:false}
      },
      scales:{ x:{ display:true }, y:{ display:true } }
    }
  };
  // add TP and SL as horizontal lines if provided
  if (tp){
    config.data.datasets.push({ label:'TP', data: labels.map(()=>tp), borderColor:'#28a745', borderDash:[6,4], fill:false, pointRadius:0 });
  }
  if (sl){
    config.data.datasets.push({ label:'SL', data: labels.map(()=>sl), borderColor:'#dc3545', borderDash:[6,4], fill:false, pointRadius:0 });
  }

  const c = new Chart(ctx, config);
  if (canvasId==='chart10') chart10 = c;
  else chart5 = c;
}

async function loadAsset(name){
  const resp = await fetch(`/api/asset?asset=${encodeURIComponent(name)}`);
  const j = await resp.json();
  const r10 = j.data.results.min_10;
  const r5  = j.data.results.min_5;
  const chart = j.data.chart || [];
  document.getElementById('updatedAt').innerText = "Updated: " + j.data.updated;
  // top
  document.getElementById('topInfo').innerText = r10.reason && r10.reason!=="ok" ? ("No trade reason: "+r10.reason) : ("Trade available: "+(r10.reason==="ok"));
  updateTable('table10', {dir_1:r10.dir_1,dir_5:r10.dir_5,dir_15:r10.dir_15,entry:r10.entry,tp:r10.tp,sl:r10.sl,confirm_1m:r10.confirm_1m});
  buildChart('chart10', chart, r10.tp?parseFloat(r10.tp):null, r10.sl?parseFloat(r10.sl):null);

  // bottom
  document.getElementById('botInfo').innerText = r5.reason && r5.reason!=="ok" ? ("No trade reason: "+r5.reason) : ("Trade available: "+(r5.reason==="ok"));
  updateTable('table5', {dir_1:r5.dir_1,dir_5:r5.dir_5,dir_15:r5.dir_15,entry:r5.entry,tp:r5.tp,sl:r5.sl,confirm_1m:r5.confirm_1m});
  buildChart('chart5', chart, r5.tp?parseFloat(r5.tp):null, r5.sl?parseFloat(r5.sl):null);
}

async function init(){
  // build menu status then populate select
  const menuResp = await fetch('/api/menu_status');
  const menuJson = await menuResp.json();
  const sel = document.getElementById('assetSelect');
  sel.innerHTML = '';
  for (const name of Object.keys(ASSETS)){
    const opt = document.createElement('option');
    opt.value = name;
    opt.text = name;
    const ok = menuJson[name] && menuJson[name].trade_available;
    opt.style.color = ok ? 'green' : 'red';
    sel.appendChild(opt);
  }
  // choose first and load
  const chosen = sel.value;
  await loadAsset(chosen);

  sel.onchange = async ()=> {
    await loadAsset(sel.value);
  };

  // auto-refresh every 60s
  setInterval(async ()=>{
    const current = sel.value;
    await loadAsset(current);
    // refresh menu coloring occasionally
    const m = await (await fetch('/api/menu_status')).json();
    for (let i=0;i<sel.options.length;i++){
      const opt = sel.options[i];
      opt.style.color = m[opt.value] && m[opt.value].trade_available ? 'green' : 'red';
    }
  }, 60*1000);
}

window.onload = init;
</script>
</body>
</html>
"""

# ---------- API route for menu coloring ----------
@app.route("/api/menu_status")
def api_menu_status():
    # lightweight test: analyze each asset but small caches will keep this reasonable
    out = {}
    for name,sym in ASSETS.items():
        try:
            res = analyze_instrument(sym)
            r10 = res["data"]["results"].get("min_10",{})
            r5  = res["data"]["results"].get("min_5",{})
            ok = (r10.get("reason")=="ok") or (r5.get("reason")=="ok")
            out[name] = {"trade_available": ok}
        except Exception:
            out[name] = {"trade_available": False}
    return jsonify(out)

# ---------- main page ----------
@app.route("/")
def index():
    return render_template_string(PAGE_HTML, assets=ASSETS, cache_ttl=CACHE_TTL)

# ---------- asset API already implemented above ----------
# /api/asset?asset=Gold%20(XAU/USD)

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
