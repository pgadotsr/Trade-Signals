# main.py
# FastAPI app — Multi-timeframe, safer-mode signals with ATR and S/R checks
# Requirements: fastapi, uvicorn, requests

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import requests, time, statistics
from datetime import datetime, timedelta

app = FastAPI()

# ------------------ USER KEYS (already provided) ------------------
ALPHA_KEY = "A25IELIDXARY4KIX"                     # Alpha Vantage (intraday candles)
METALS_KEY = "60b73c22f4da4cdb961fd410b2c57fa4"    # Metals-API (latest / timeseries when available)
# -----------------------------------------------------------------

# cache to reduce calls and stay within rate limits
CACHE = {}
CACHE_TTL = 55  # seconds

def cache_get(k):
    rec = CACHE.get(k)
    if rec and time.time() - rec["ts"] < CACHE_TTL:
        return rec["v"]
    return None

def cache_set(k, v):
    CACHE[k] = {"v": v, "ts": time.time()}
    return v

# ------------------ Assets ------------------
# symbol formats used by the providers
ASSET_OPTIONS = {
    "XAU/USD": {"type": "metal", "symbol": "XAU"},
    "XAG/USD": {"type": "metal", "symbol": "XAG"},
    "GBP/USD": {"type": "fx", "from": "GBP", "to": "USD"},
    "EUR/USD": {"type": "fx", "from": "EUR", "to": "USD"},
    "USD/JPY": {"type": "fx", "from": "USD", "to": "JPY"}
}

# ------------------ Helpers: fetch candles / prices ------------------

def fetch_fx_candles(from_sym, to_sym, interval):
    """Alpha Vantage FX_INTRADAY returns time series; we return list[(ts_str, close)] oldest->newest"""
    key = f"fx:{from_sym}{to_sym}:{interval}"
    cached = cache_get(key)
    if cached:
        return cached
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
        # find the "Time Series" key
        series_key = next((k for k in j.keys() if k.startswith("Time Series")), None)
        if not series_key:
            return None
        series = j[series_key]
        items = []
        for ts in sorted(series.keys()):
            close = float(series[ts]['4. close'])
            items.append((ts, close))
        return cache_set(key, items)
    except Exception:
        return None

def fetch_metal_latest(symbol):
    """Get latest metal spot (Metals-API). Returns float price (USD) or None"""
    key = f"metal_latest:{symbol}"
    cached = cache_get(key)
    if cached:
        return cached
    url = "https://metals-api.com/api/latest"
    params = {"access_key": METALS_KEY, "base": "USD", "symbols": symbol}
    try:
        r = requests.get(url, params=params, timeout=10)
        j = r.json()
        if "rates" in j and symbol in j["rates"]:
            # metals-api returns rate = USD per 1 unit of base? earlier we used direct value; keep consistent
            val = float(j["rates"][symbol])
            cache_set(key, val)
            return val
    except Exception:
        pass
    return None

def fetch_metal_timeseries(symbol):
    """Try Metals-API timeseries endpoint (may not be available on free plan). Return list[(ts,close)] or None"""
    key = f"metal_ts:{symbol}"
    cached = cache_get(key)
    if cached:
        return cached
    url = "https://metals-api.com/api/timeseries"
    end = datetime.utcnow().date()
    start = end - timedelta(days=10)
    params = {"access_key": METALS_KEY, "start_date": start.isoformat(), "end_date": end.isoformat(), "symbols": symbol, "base": "USD"}
    try:
        r = requests.get(url, params=params, timeout=12)
        j = r.json()
        if "rates" in j:
            series = []
            for date_str in sorted(j["rates"].keys()):
                rates = j["rates"][date_str]
                if symbol in rates:
                    series.append((date_str, float(rates[symbol])))
            if series:
                return cache_set(key, series)
    except Exception:
        pass
    return None

# ------------------ Analysis helpers ------------------

def moving_average_from_candles(candles, period):
    if not candles or len(candles) < period:
        return None
    closes = [c for _, c in candles]
    return statistics.mean(closes[-period:])

def approx_atr_from_closes(candles, period=14):
    # approximate ATR by average absolute close-to-close move (good enough for volatility filter)
    if not candles or len(candles) < period+1:
        return None
    closes = [c for _, c in candles]
    trs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
    return statistics.mean(trs[-period:])

def find_local_supports_resistances(candles, lookback=30):
    """Return supports (ascending nearest first) and resistances for recent lookback using local minima/max"""
    if not candles or len(candles) < 5:
        return [], []
    closes = [c for _, c in candles]
    n = len(closes)
    supports = []
    resistances = []
    # local min/max with window 5
    for i in range(2, n-2):
        window = closes[i-2:i+3]
        val = closes[i]
        if val == min(window):
            supports.append((i, val))
        if val == max(window):
            resistances.append((i, val))
    # sort by proximity to most recent
    supports.sort(key=lambda x: abs(n-1 - x[0]))
    resistances.sort(key=lambda x: abs(n-1 - x[0]))
    return [v for _, v in supports], [v for _, v in resistances]

def short_long_signal_from_candles(candles, short_p=3, long_p=8):
    """Simple signal: compare short MA vs long MA on close prices"""
    if not candles or len(candles) < long_p + 1:
        return None
    closes = [c for _, c in candles]
    short_ma = statistics.mean(closes[-short_p:])
    long_ma = statistics.mean(closes[-long_p:])
    if short_ma > long_ma:
        return "BUY"
    elif short_ma < long_ma:
        return "SELL"
    else:
        return "HOLD"

def one_min_momentum(candles_1m):
    """Return True if very short momentum is upswing (last > prev), False if downswing, None if unknown"""
    if not candles_1m or len(candles_1m) < 2:
        return None
    closes = [c for _, c in candles_1m]
    return closes[-1] > closes[-2]

# ------------------ High level analyze ------------------

def analyze_symbol(asset_key):
    """Return analysis dict for the chosen asset_key (e.g. 'XAU/USD' or 'EUR/USD')"""
    cfg = ASSET_OPTIONS.get(asset_key)
    if not cfg:
        return {"error": "unsupported asset"}

    out = {"asset": asset_key, "timestamp": datetime.utcnow().isoformat()+"Z"}
    # 1) fetch data per asset type
    if cfg["type"] == "fx":
        from_sym = cfg["from"]
        to_sym = cfg["to"]
        # get candles: 1h, 15min, 5min, 1min
        c1h = fetch_fx_candles(from_sym, to_sym, "60min") or []
        c15 = fetch_fx_candles(from_sym, to_sym, "15min") or []
        c5 = fetch_fx_candles(from_sym, to_sym, "5min") or []
        c1 = fetch_fx_candles(from_sym, to_sym, "1min") or []
        # current price fallback
        current_price = c1[-1][1] if c1 else (c5[-1][1] if c5 else (c15[-1][1] if c15 else (c1h[-1][1] if c1h else None)))
    else:
        # metal: try timeseries (daily) and latest spot. Many free metals plans lack intraday timeseries.
        ts = fetch_metal_timeseries(cfg["symbol"])  # may be daily
        latest = fetch_metal_latest(cfg["symbol"])
        # Build synthetic 1h/15m/5m/1m candles if timeseries not available by repeating latest
        if ts:
            # ts is likely daily; use it as coarse 1h series fallback
            c1h = ts[-120:] if len(ts) >= 60 else ts
            c15 = ts[-50:]  # approximate
            c5 = ts[-20:]
            c1 = ts[-6:]
        else:
            # fallback: replicate latest into small lists
            if latest is None:
                return {"error": "no price available"}
            now = datetime.utcnow()
            def make(k):
                arr = []
                for i in range(k, 0, -1):
                    arr.append(((now - timedelta(minutes=i)).isoformat()), latest)
                # incorrect formatting avoided by building tuples correctly below
            # simpler: basic lists of (ts,price)
            c1h = [( (datetime.utcnow() - timedelta(hours=i)).isoformat(), latest) for i in range(60, -1, -1)]
            c15 = [( (datetime.utcnow() - timedelta(minutes=15*i)).isoformat(), latest) for i in range(16, -1, -1)]
            c5  = [( (datetime.utcnow() - timedelta(minutes=5*i)).isoformat(), latest) for i in range(12, -1, -1)]
            c1  = [( (datetime.utcnow() - timedelta(minutes=i)).isoformat(), latest) for i in range(6, -1, -1)]
        # ensure variables exist
        try:
            c1h
        except NameError:
            c1h, c15, c5, c1 = [], [], [], []
        current_price = latest or (c1[-1][1] if c1 else None)

    out["current_price"] = current_price

    # 2) primary trend (1h MA50)
    ma50 = moving_average_from_candles(c1h, 50) if c1h else None
    if ma50:
        diff_pct = (current_price - ma50) / ma50 * 100
        if diff_pct > 0.5:
            primary = "UP"
        elif diff_pct < -0.5:
            primary = "DOWN"
        else:
            primary = "SIDEWAYS"
    else:
        primary = "UNKNOWN"
    out["primary_trend"] = primary
    out["ma50"] = ma50

    # 3) short-term signals (15m & 5m)
    sig15 = short_long_signal_from_candles(c15) if c15 else None
    sig5  = short_long_signal_from_candles(c5) if c5 else None

    out["sig15"] = sig15
    out["sig5"] = sig5

    # require agreement 15 & 5
    if sig15 and sig5 and sig15 == sig5 and sig15 in ("BUY","SELL"):
        agreed = True
        agreed_side = sig15
    else:
        agreed = False
        agreed_side = None
    out["agreed"] = agreed
    out["agreed_side"] = agreed_side

    # 4) ATR volatility (use 15m closes to compute ATR)
    atr = approx_atr_from_closes(c15 or c5 or c1h or [])
    out["atr"] = atr

    # block if ATR too small relative to price (arbitrary threshold: atr_pct < 0.02%)
    atr_ok = False
    if atr and current_price:
        atr_pct = (atr / current_price) * 100
        atr_ok = atr_pct > 0.02  # small heuristic; adjust later
        out["atr_pct"] = atr_pct
    else:
        out["atr_pct"] = None

    # 5) support/resistance using 15m & 5m combined
    supports, resistances = find_local_supports_resistances((c15 or []) + (c5 or []), lookback=30)
    nearest_support = supports[0] if supports else None
    nearest_resist = resistances[0] if resistances else None
    out["nearest_support"] = nearest_support
    out["nearest_resistance"] = nearest_resist

    # 6) Build trade suggestion only if agreed and ATR OK
    trade = None
    if agreed and atr_ok and current_price:
        # entry = current price
        entry = current_price
        if agreed_side == "BUY":
            # TP = nearest resistance above price if exists else price + 2*atr
            tp = nearest_resist if nearest_resist and nearest_resist > entry else (entry + 2 * (atr or (0.001*entry)))
            sl = entry - ( (tp - entry) * 0.5 ) if tp else entry - (1.5 * (atr or 0.001*entry))
            trade = {"side": "BUY", "entry": entry, "tp": tp, "sl": sl}
        else:
            tp = nearest_support if nearest_support and nearest_support < entry else (entry - 2 * (atr or (0.001*entry)))
            sl = entry + ( (entry - tp) * 0.5 ) if tp else entry + (1.5 * (atr or 0.001*entry))
            trade = {"side": "SELL", "entry": entry, "tp": tp, "sl": sl}
        out["trade_candidate"] = trade
    else:
        out["trade_candidate"] = None

    # 7) 1-min confirmation: momentum check
    momentum_1m = None
    if 'fx' in cfg["type"] if (cfg:=cfg) else False:  # to satisfy linter but safe
        pass
    try:
        # we already have c1 for fx; for metal, c1 is synthetic
        momentum = None
        if cfg["type"] == "fx":
            from_sym, to_sym = cfg["from"], cfg["to"]
            c1 = fetch_fx_candles(from_sym, to_sym, "1min") or []
            momentum = one_min_momentum(c1)
        else:
            # metals: 1m not available in many providers; approximate with last two closes from c5 if necessary
            c1 = c1 if 'c1' in locals() else []
            if not c1 and c5:
                # use last two of 5m as proxy
                c1 = c5[-2:]
            momentum = one_min_momentum(c1)
        out["one_min_momentum"] = momentum  # True / False / None
    except Exception:
        out["one_min_momentum"] = None

    # Final flag: confirmed if trade exists and 1min momentum agrees with side
    if trade and out["one_min_momentum"] is not None:
        if trade["side"] == "BUY" and out["one_min_momentum"] is True:
            trade["confirmed"] = True
        elif trade["side"] == "SELL" and out["one_min_momentum"] is False:
            trade["confirmed"] = True
        else:
            trade["confirmed"] = False
    elif trade:
        trade["confirmed"] = None

    return out

# ------------------ Web endpoints ------------------

@app.get("/", response_class=HTMLResponse)
def homepage():
    # HTML page with dropdown and a result area; JS calls /analyze?asset=...
    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Signals — MultiTF Safer Mode</title>
      <style>
        body {{ font-family: Arial; background:#0b0b0b; color:#eee; padding:12px; }}
        .card{{background:#111;padding:12px;border-radius:8px;margin:8px 0;}}
        select, button {{font-size:16px;padding:8px;margin:6px;}}
        .small {{color:#aaa;font-size:0.9em}}
      </style>
    </head>
    <body>
      <h2>Trade Signals — Safer Mode</h2>
      <div class="small">Primary bias = 1h MA50. Signals require 15m & 5m agreement + ATR filter. 1m confirmation shown.</div>
      <div style="margin-top:10px">
        <label for="asset">Asset:</label>
        <select id="asset">
          {''.join([f'<option value="{k}" {"selected" if k=="XAU/USD" else ""}>{k}</option>' for k in ASSET_OPTIONS.keys()])}
        </select>
        <button onclick="load()">Get</button>
      </div>

      <div id="result" style="margin-top:12px"></div>

      <script>
        async function load(){
          const asset = document.getElementById('asset').value;
          document.getElementById('result').innerHTML = '<div class="card">Loading...</div>';
          try {{
            const res = await fetch('/analyze?asset=' + encodeURIComponent(asset));
            const j = await res.json();
            if (j.error) {{
              document.getElementById('result').innerHTML = '<div class="card">Error: ' + j.error + '</div>';
              return;
            }}
            // build display
            let html = '<div class="card"><strong>' + j.asset + '</strong><br>';
            html += '<div class="small">As of: ' + j.timestamp + '</div>';
            html += '<div>Primary trend (1h): <b>' + j.primary_trend + '</b>';
            if (j.ma50) html += ' &nbsp; MA50: ' + (j.ma50 ? j.ma50.toFixed(6) : '');
            html += '</div>';
            html += '<div>Price: <b>' + (j.current_price ? (typeof j.current_price === "number" ? j.current_price.toFixed(6) : j.current_price) : "N/A") + '</b></div>';
            html += '<hr>';
            html += '<div><b>Short-term agreement:</b> 15m=' + (j.sig15||"N/A") + ' | 5m=' + (j.sig5||"N/A") + '</div>';
            html += '<div class="small">ATR%: ' + (j.atr_pct ? j.atr_pct.toFixed(4) + '%' : 'N/A') + '</div>';
            if (j.trade_candidate) {{
              const t = j.trade_candidate;
              html += '<hr><div><b>Signal:</b> ' + t.side + ' @ ' + t.entry.toFixed(6) + '</div>';
              html += '<div>TP: ' + (t.tp ? t.tp.toFixed(6) : 'N/A') + ' | SL: ' + (t.sl ? t.sl.toFixed(6) : 'N/A') + '</div>';
              html += '<div class="small">Confirmed by 1m: ' + (t.confirmed === true ? '✅' : (t.confirmed === false ? '❌' : 'Unknown')) + '</div>';
            }} else {{
              html += '<hr><div><b>No trade candidate</b> — no agreement / low ATR / insufficient data</div>';
            }}
            if (j.nearest_resistance) html += '<div class="small">Nearest resistance: ' + j.nearest_resistance.toFixed(6) + '</div>';
            if (j.nearest_support) html += '<div class="small">Nearest support: ' + j.nearest_support.toFixed(6) + '</div>';
            html += '</div>';
            document.getElementById('result').innerHTML = html;
          }} catch (e) {{
            document.getElementById('result').innerHTML = '<div class="card">Error fetching data</div>';
            console.error(e);
          }}
        }
        // load default on open
        window.onload = load;
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/analyze")
def analyze(asset: str = Query(...)):
    if asset not in ASSET_OPTIONS:
        return JSONResponse({"error": "unsupported asset"}, status_code=400)
    try:
        res = analyze_symbol(asset)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
