# main.py
# OANDA multi-timeframe signals (1m, 5m, 15m, 1h) with ATR-based TP/SL
# Requirements: flask, requests, pandas, numpy, ta, gunicorn
# Start with: gunicorn main:app

import os
import time
from datetime import datetime
from flask import Flask, render_template_string
import requests
import pandas as pd
import numpy as np
import ta

app = Flask(__name__)

# ---------- Config ----------
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")  # "practice" or "live"
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")

OANDA_BASE = "https://api-fxpractice.oanda.com/v3" if OANDA_ENV == "practice" else "https://api-fxtrade.oanda.com/v3"
CACHE_TTL = 25  # seconds, short cache to reduce calls but keep near-real-time
# Instruments shown on the page
ASSETS = {
    "Gold (XAU/USD)": "XAU_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY"
}
# ----------------------------

# Simple in-memory cache
CACHE = {}

def cache_get(key):
    rec = CACHE.get(key)
    if rec and time.time() - rec["ts"] < CACHE_TTL:
        return rec["val"]
    return None

def cache_set(key, val):
    CACHE[key] = {"val": val, "ts": time.time()}

# ---------- OANDA helper ----------
def fetch_oanda_candles(instrument, granularity="M5", count=200):
    """
    Returns pandas DataFrame of candles (oldest->newest) or None on error.
    granularity = "M1","M5","M15","H1"
    """
    key = f"{instrument}:{granularity}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    url = f"{OANDA_BASE}/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    params = {
        "granularity": granularity,
        "count": count,
        "price": "M"  # use mid prices
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        r.raise_for_status()
        j = r.json()
        candles = j.get("candles")
        if not candles:
            cache_set(key, None)
            return None

        rows = []
        for c in candles:
            # only use completed candles for stable indicators
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

# ---------- Indicators & signals ----------
def compute_atr(df, period=14):
    """Return ATR (last value) or None"""
    try:
        atr = ta.volatility.AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=period).average_true_range()
        return float(atr.iloc[-1])
    except Exception:
        # fallback simpler
        try:
            closes = df["close"].values
            diffs = np.abs(np.diff(closes))
            return float(np.mean(diffs[-period:])) if len(diffs) > 0 else None
        except Exception:
            return None

def signal_from_ema(df, fast=9, slow=21):
    """Return 'BUY'/'SELL'/'NEUTRAL' based on EMA crossover latest"""
    if df is None or len(df) < slow + 2:
        return None
    try:
        ema_fast = ta.trend.ema_indicator(df["close"], window=fast)
        ema_slow = ta.trend.ema_indicator(df["close"], window=slow)
        f = ema_fast.iloc[-1]
        s = ema_slow.iloc[-1]
        return "BUY" if f > s else "SELL"
    except Exception:
        return None

def one_min_confirmation(df_1m):
    """Return 'Up'/'Down'/'Flat' or None"""
    if df_1m is None or len(df_1m) < 2:
        return None
    last = df_1m["close"].iloc[-1]
    prev = df_1m["close"].iloc[-2]
    if last > prev: return "Up"
    if last < prev: return "Down"
    return "Flat"

# Build TP/SL using ATR (different for Buy vs Sell)
def atr_targets(price, atr, direction):
    """
    Returns (tp, sl). For BUY: tp = price + atr*mult_tp, sl=price - atr*mult_sl
    For SELL: tp = price - atr*mult_tp, sl=price + atr*mult_sl
    """
    if atr is None or atr == 0:
        return (None, None)
    mult_tp = 1.5
    mult_sl = 1.0
    if direction == "BUY":
        tp = price + atr * mult_tp
        sl = price - atr * mult_sl
    elif direction == "SELL":
        tp = price - atr * mult_tp
        sl = price + atr * mult_sl
    else:
        return (None, None)
    return (round(float(tp), 6), round(float(sl), 6))

# ---------- Analysis per asset ----------
def analyze_asset(oanda_symbol):
    """
    Returns analysis dict for one asset with fields:
    - primary_bias (1h)
    - rows: list for 1m,5m,15m each {tf,direction,entry,tp,sl,confirm}
    - agreement (15m vs 5m), trade_candidate (string), updated timestamp
    """
    # fetch timeframes
    df_1h = fetch_oanda_candles(oanda_symbol, "H1", count=200)
    df_15 = fetch_oanda_candles(oanda_symbol, "M15", count=200)
    df_5  = fetch_oanda_candles(oanda_symbol, "M5", count=200)
    df_1  = fetch_oanda_candles(oanda_symbol, "M1", count=200)

    result = {
        "primary_bias": "UNKNOWN",
        "ma50": None,
        "rows": [],
        "agreement": "N/A",
        "trade_candidate": "None",
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }

    # Primary 1h bias using MA50
    try:
        if df_1h is not None and len(df_1h) >= 60:
            ma50 = df_1h["close"].rolling(window=50).mean().iloc[-1]
            current = float(df_1h["close"].iloc[-1])
            result["ma50"] = round(float(ma50), 6)
            diff_pct = (current - ma50) / ma50 * 100
            if diff_pct > 0.5:
                result["primary_bias"] = "UP"
            elif diff_pct < -0.5:
                result["primary_bias"] = "DOWN"
            else:
                result["primary_bias"] = "SIDEWAYS"
    except Exception:
        pass

    # analyze each timeframe
    tfs = [("1m", df_1), ("5m", df_5), ("15m", df_15)]
    sigs = {}
    for tf_name, df in tfs:
        if df is None or len(df) < 10:
            sigs[tf_name] = {"direction":"N/A","entry":"N/A","tp":"N/A","sl":"N/A","confirm":"N/A","row_class":"neutral"}
            continue

        direction = signal_from_ema(df) or "N/A"
        last_price = float(df["close"].iloc[-1])
        atr = compute_atr(df, period=14)
        tp, sl = atr_targets(last_price, atr, direction) if direction in ("BUY","SELL") else (None,None)

        # 1m confirmation (from df_1)
        conf = "N/A"
        if df_1 is not None and len(df_1) >= 2:
            c = one_min_confirmation(df_1)
            conf = c if c is not None else "N/A"

        sigs[tf_name] = {
            "direction": direction,
            "entry": f"{last_price:.6f}",
            "tp": f"{tp:.6f}" if tp is not None else "N/A",
            "sl": f"{sl:.6f}" if sl is not None else "N/A",
            "atr": round(atr,6) if atr else None,
            "row_class": "buy" if direction=="BUY" else "sell" if direction=="SELL" else "neutral",
            "confirm": conf
        }

    # agreement between 15m and 5m
    sig15 = sigs["15m"]["direction"]
    sig5  = sigs["5m"]["direction"]
    if sig15 in ("BUY","SELL") and sig5 in ("BUY","SELL") and sig15 == sig5:
        result["agreement"] = sig15
        agreement = True
    else:
        result["agreement"] = "No agreement"
        agreement = False

    # candidate: require 15m & 5m agreement AND 1m direction to match (confirmation)
    candidate = None
    if agreement:
        conf1 = sigs["1m"]["direction"]
        if conf1 in ("BUY","SELL") and conf1 == sig15:
            # use entry from 1m
            entry = sigs["1m"]["entry"]
            tp = sigs["1m"]["tp"]
            sl = sigs["1m"]["sl"]
            candidate = f"{sig15} @ {entry}  TP:{tp}  SL:{sl}"
    result["trade_candidate"] = candidate if candidate else "None"

    # prepare rows for display
    rows = []
    for tf in ["1m","5m","15m"]:
        d = sigs[tf]
        rows.append({
            "tf": tf,
            "direction": d["direction"],
            "entry": d["entry"],
            "tp": d["tp"],
            "sl": d["sl"],
            "confirm": d["confirm"],
            "row_class": d["row_class"]
        })

    result["rows"] = rows
    return result

# ---------- HTML template (mobile-friendly, similar to previous design) ----------
HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Trade Signals</title>
  <style>
    body{font-family:Arial;background:#fff;color:#111;margin:14px}
    h1,h2{ text-align:center }
    .asset-select{margin:8px 0;text-align:center}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:900px;margin:12px auto}
    .card{padding:12px;border-radius:6px;background:#f4f4f4}
    table{width:100%;border-collapse:collapse;margin-top:8px}
    th,td{padding:8px;border:1px solid #ddd;text-align:center}
    th{background:#eee}
    .buy{background:#e6ffed}
    .sell{background:#ffecec}
    .neutral{background:#fafafa}
    .small{font-size:13px;color:#555}
  </style>
</head>
<body>
  <h1>Trade Signals</h1>
  <div class="asset-select">
    <form method="get">
      Asset:
      <select name="asset" onchange="this.form.submit()">
        {% for name in assets %}
          <option value="{{name}}" {% if name==selected %}selected{% endif %}>{{name}}</option>
        {% endfor %}
      </select>
    </form>
  </div>

  <h2>Metals / Forex</h2>
  <div style="max-width:900px;margin:0 auto" class="small">
    Primary bias = 1h MA50. Signals require 15m & 5m agreement; 1m for confirmation. Page caches briefly to avoid hitting API limits.
  </div>

  <div style="max-width:900px;margin:12px auto">
    <div style="margin-top:10px"><strong>Primary 1h bias:</strong> {{ primary_bias }} {% if ma50 %}| MA50: {{ ma50 }}{% endif %}</div>

    <table>
      <tr><th>TF</th><th>Direction</th><th>Entry</th><th>Take Profit</th><th>Stop Loss</th><th>1m Confirm</th></tr>
      {% for r in rows %}
      <tr class="{{ r.row_class }}">
        <td style="font-weight:bold">{{ r.tf }}</td>
        <td>{{ r.direction }}</td>
        <td>{{ r.entry }}</td>
        <td style="font-weight:bold">{{ r.tp }}</td>
        <td style="font-weight:bold">{{ r.sl }}</td>
        <td>{{ r.confirm }}</td>
      </tr>
      {% endfor %}
    </table>

    <div style="margin-top:12px" class="small">
      Agreement (15m vs 5m): <strong>{{ agreement }}</strong><br>
      Trade candidate: <strong>{{ trade_candidate }}</strong>
    </div>

    <div style="margin-top:10px" class="small">Last updated: {{ updated }}</div>
  </div>
</body>
</html>
"""

# ---------- Routes ----------
@app.route("/", methods=["GET"])
def index():
    selected = (list(ASSETS.keys())[0])
    # allow query param "asset" to change symbol
    # Flask's request is imported lazily to avoid circular at top
    from flask import request
    sel = request.args.get("asset")
    if sel and sel in ASSETS:
        selected = sel

    symbol = ASSETS[selected]
    analysis = analyze_asset(symbol)
    return render_template_string(HTML,
                                  assets=ASSETS.keys(),
                                  selected=selected,
                                  primary_bias=analysis.get("primary_bias"),
                                  ma50=analysis.get("ma50"),
                                  rows=analysis["rows"],
                                  agreement=analysis.get("agreement"),
                                  trade_candidate=analysis.get("trade_candidate"),
                                  updated=analysis.get("updated"))

@app.route("/api/signal", methods=["GET"])
def api_signal():
    from flask import request
    asset = request.args.get("asset", list(ASSETS.keys())[0])
    if asset not in ASSETS:
        return {"error":"unknown asset"},400
    return analyze_asset(ASSETS[asset])

# ---------- Run ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
