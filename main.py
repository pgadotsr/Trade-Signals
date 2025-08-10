# main.py
# Flask app — Combined multi-timeframe signals (1m / 5m / 15m) with ATR-based TP/SL and 1h bias
# Paste into your repo root. Start on Render with: gunicorn main:app

from flask import Flask, request, render_template_string
import requests
import time
import pandas as pd
import numpy as np
import ta

app = Flask(__name__)

# -------------------- CONFIG --------------------
ALPHA_KEY = "A25IELIDXARY4KIX"   # Your Alpha Vantage key
CACHE = {}
CACHE_TTL = 55  # seconds, to respect rate limits

ASSETS = {
    "Gold (XAU/USD)": "XAUUSD",
    "Silver (XAG/USD)": "XAGUSD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY"
}
# ------------------------------------------------

HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>Multi-TF Signals</title>
  <style>
    body{font-family:Arial;margin:12px;color:#111}
    table{width:100%;border-collapse:collapse}
    th,td{padding:8px;border:1px solid #ddd;text-align:center;font-size:14px}
    th{background:#f4f4f4}
    .buy{background:#e6ffed}
    .sell{background:#ffecec}
    .neutral{background:#f7f7f7}
    .small{font-size:12px;color:#666}
    .big {font-weight:bold;font-size:16px}
  </style>
</head>
<body>
  <h2>Signals — Combined 1m / 5m / 15m</h2>
  <form method="get">
    Asset:
    <select name="asset" onchange="this.form.submit()">
      {% for n in assets %}
        <option value="{{n}}" {% if n==selected %}selected{% endif %}>{{n}}</option>
      {% endfor %}
    </select>
  </form>

  <p class="small">Primary bias = 1h MA50. Signals require 15m & 5m agreement to propose a trade; 1m is confirmation. Page auto-refreshes every 60s.</p>

  <div style="margin-top:10px">
    <strong>Primary 1h bias:</strong> {{ primary_bias }} {% if ma50 %}| MA50: {{ "%.6f"|format(ma50) }}{% endif %}
  </div>

  <table>
    <tr><th>TF</th><th>Direction</th><th>Entry</th><th>Take Profit</th><th>Stop Loss</th><th>1m Confirm</th></tr>
    {% for row in rows %}
      <tr class="{{ row.row_class }}">
        <td class="big">{{ row.tf }}</td>
        <td>{{ row.direction }}</td>
        <td>{{ row.entry }}</td>
        <td class="big">{{ row.tp }}</td>
        <td class="big">{{ row.sl }}</td>
        <td>{{ row.confirm }}</td>
      </tr>
    {% endfor %}
  </table>

  <div style="margin-top:12px" class="small">
    Agreement (15m vs 5m): <strong>{{ agreement }}</strong><br>
    Trade candidate: <strong>{{ trade_candidate }}</strong>
  </div>

  <p class="small" style="margin-top:14px;">Last updated: {{ updated }}</p>
</body>
</html>
"""

# -------------------- CACHING --------------------
def cache_get(key):
    v = CACHE.get(key)
    if v and time.time() - v['ts'] < CACHE_TTL:
        return v['val']
    return None

def cache_set(key, val):
    CACHE[key] = {'val': val, 'ts': time.time()}
    return val
# -------------------------------------------------

# -------------------- Data fetch -----------------
def fetch_alpha_intraday(symbol, interval):
    """
    Returns a DataFrame (oldest->newest) or None.
    symbol example: 'EURUSD' or 'XAUUSD' (may not exist for some providers).
    """
    cache_key = f"{symbol}:{interval}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    from_sym = symbol[:3]
    to_sym = symbol[3:]
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_sym,
        "to_symbol": to_sym,
        "interval": interval,
        "outputsize": "compact",
        "apikey": ALPHA_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        j = r.json()
    except Exception:
        cache_set(cache_key, None)
        return None

    # find the "Time Series" key
    series_key = next((k for k in j.keys() if k.startswith("Time Series")), None)
    if not series_key:
        cache_set(cache_key, None)
        return None

    series = j[series_key]
    try:
        df = pd.DataFrame.from_dict(series, orient='index', dtype=float)
        # normalize column names like "1. open" -> "open"
        df.columns = [c.split()[-1] for c in df.columns]
        df.index = pd.to_datetime(df.index)
        df = df.rename(columns=lambda s: s.lower())
        df = df.sort_index()
        cache_set(cache_key, df)
        return df
    except Exception:
        cache_set(cache_key, None)
        return None
# -------------------------------------------------

# -------------------- Indicators / signals ----------------
def compute_atr(df, period=14):
    try:
        atr_series = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=period).average_true_range()
        return float(atr_series.iloc[-1])
    except Exception:
        closes = df['close'].values
        if len(closes) < 2:
            return None
        diffs = np.abs(np.diff(closes))
        return float(np.mean(diffs[-period:])) if len(diffs) >= 1 else None

def signal_from_emas(df, fast=9, slow=21):
    if df is None or len(df) < slow + 1:
        return None
    try:
        ema_fast = ta.trend.ema_indicator(df['close'], window=fast).iloc[-1]
        ema_slow = ta.trend.ema_indicator(df['close'], window=slow).iloc[-1]
        return "BUY" if ema_fast > ema_slow else "SELL"
    except Exception:
        return None

def one_min_confirmation(df1m):
    if df1m is None or len(df1m) < 2:
        return None
    last = float(df1m['close'].iloc[-1])
    prev = float(df1m['close'].iloc[-2])
    if last > prev:
        return "Up"
    elif last < prev:
        return "Down"
    else:
        return "Flat"
# ---------------------------------------------------------

# -------------------- Analysis --------------------------
def analyze(symbol):
    """Return structure used by template."""
    # fetch needed frames
    df_1h = fetch_alpha_intraday(symbol, "60min")
    df_15 = fetch_alpha_intraday(symbol, "15min")
    df_5 = fetch_alpha_intraday(symbol, "5min")
    df_1 = fetch_alpha_intraday(symbol, "1min")

    result = {
        "primary_bias": "UNKNOWN",
        "ma50": None,
        "rows": [],
        "agreement": "N/A",
        "trade_candidate": "None",
        "updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    }

    # 1h bias (MA50)
    if df_1h is not None and len(df_1h) >= 60:
        ma50 = df_1h['close'].rolling(window=50).mean().iloc[-1]
        current = float(df_1h['close'].iloc[-1])
        result['ma50'] = ma50
        diff_pct = (current - ma50) / ma50 * 100
        if diff_pct > 0.5:
            result['primary_bias'] = "UP"
        elif diff_pct < -0.5:
            result['primary_bias'] = "DOWN"
        else:
            result['primary_bias'] = "SIDEWAYS"

    # timeframes to analyze
    tfs = [("1m", df_1), ("5m", df_5), ("15m", df_15)]
    tf_signals = {}

    for tf_name, df in tfs:
        if df is None or len(df) < 3:
            tf_signals[tf_name] = {"direction": "N/A", "entry": "N/A", "tp": "N/A", "sl": "N/A", "confirm": "N/A", "row_class": "neutral"}
            continue

        direction = signal_from_emas(df)
        last_price = float(df['close'].iloc[-1])
        atr = compute_atr(df, period=14) or 0.0

        if direction == "BUY":
            tp = last_price + atr * 1.5
            sl = last_price - atr * 1.0
            row_class = "buy"
        elif direction == "SELL":
            tp = last_price - atr * 1.5
            sl = last_price + atr * 1.0
            row_class = "sell"
        else:
            tp = None; sl = None; row_class = "neutral"

        conf = one_min_confirmation(df_1) if df_1 is not None else None
        conf_str = conf if conf is not None else "N/A"

        tf_signals[tf_name] = {
            "direction": direction or "N/A",
            "entry": f"{last_price:.6f}",
            "tp": f"{tp:.6f}" if tp is not None else "N/A",
            "sl": f"{sl:.6f}" if sl is not None else "N/A",
            "atr": round(atr, 6) if atr else None,
            "row_class": row_class,
            "confirm": conf_str
        }

    # agreement between 15m and 5m
    sig15 = tf_signals["15m"]["direction"]
    sig5 = tf_signals["5m"]["direction"]
    if sig15 in ("BUY","SELL") and sig5 in ("BUY","SELL") and sig15 == sig5:
        result['agreement'] = sig15
        agreement = True
    else:
        result['agreement'] = "No agreement"
        agreement = False

    # trade candidate if agreement and 1m confirmation aligns with agreed side
    candidate = None
    if agreement:
        side = sig15
        conf1 = tf_signals["1m"]["direction"]
        # require 1m to match agreed side as confirmation (safer mode)
        if conf1 == side:
            candidate = f"{side} @ {tf_signals['1m']['entry']}  TP:{tf_signals['1m']['tp']}  SL:{tf_signals['1m']['sl']}"
    result['trade_candidate'] = candidate if candidate else "None"

    # build rows (1m,5m,15m)
    rows = []
    for tf in ["1m","5m","15m"]:
        d = tf_signals[tf]
        rows.append({
            "tf": tf,
            "direction": d["direction"],
            "entry": d["entry"],
            "tp": d["tp"],
            "sl": d["sl"],
            "confirm": d["confirm"],
            "row_class": d["row_class"]
        })

    result['rows'] = rows
    return result
# ---------------------------------------------------------

# -------------------- Routes -------------------------
@app.route("/", methods=["GET"])
def home():
    selected = request.args.get("asset", list(ASSETS.keys())[0])
    if selected not in ASSETS:
        selected = list(ASSETS.keys())[0]
    symbol = ASSETS[selected]
    analysis = analyze(symbol)
    return render_template_string(HTML,
                                  assets=ASSETS.keys(),
                                  selected=selected,
                                  primary_bias=analysis.get('primary_bias'),
                                  ma50=analysis.get('ma50'),
                                  rows=analysis['rows'],
                                  agreement=analysis['agreement'],
                                  trade_candidate=analysis['trade_candidate'],
                                  updated=analysis['updated'])

@app.route("/api/signal", methods=["GET"])
def api_signal():
    asset = request.args.get("asset", list(ASSETS.keys())[0])
    if asset not in ASSETS:
        return {"error":"unknown asset"}, 400
    return analyze(ASSETS[asset])

import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

@app.route("/test-alpha")
def test_alpha():
    symbol = "XAUUSD"  # Gold price in USD
    interval = "1min"

    url = (
        f"https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_INTRADAY"
        f"&symbol={symbol}"
        f"&interval={interval}"
        f"&apikey={ALPHA_VANTAGE_API_KEY}"
    )

    r = requests.get(url)
    try:
        data = r.json()
    except Exception as e:
        return jsonify({"error": str(e)})

    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
# ---------------------------------------------------------
