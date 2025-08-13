
# main_update4_diag.py
# Chart backend with NY AM High, FVGs, swings, instant-entry + diagnostics & demo mode.
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify, send_from_directory, make_response
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# ---------- Config ----------
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")  # "live" or "practice"
OANDA_BASE = "https://api-fxtrade.oanda.com" if OANDA_ENV == "live" else "https://api-fxpractice.oanda.com"

DEFAULT_TIMEFRAME = "15m"
DEFAULT_RANGE = "1D"
NY_TZ = ZoneInfo("America/New_York")

INSTRUMENT_MAP = {
    "Gold": "XAU_USD",
    "Silver": "XAG_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "Bitcoin": "BTC_USD",
    "Tesla": "TSLA_USD",
}

GRANULARITY_MAP = {"1m": "M1", "5m": "M5", "15m": "M15"}

def oanda_headers():
    if not OANDA_API_KEY:
        return {}
    return {"Authorization": f"Bearer {OANDA_API_KEY}"}

def fetch_ohlc(asset: str, timeframe: str = DEFAULT_TIMEFRAME, count: int = 500):
    instrument = INSTRUMENT_MAP.get(asset)
    if not instrument:
        raise ValueError(f"Unsupported asset: {asset}")
    gran = GRANULARITY_MAP.get(timeframe, "M15")
    url = f"{OANDA_BASE}/v3/instruments/{instrument}/candles"
    params = {"granularity": gran, "count": count, "price": "M"}
    r = requests.get(url, headers=oanda_headers(), params=params, timeout=25)
    # If unauthorized or bad instrument, raise with more detail
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"OANDA error {r.status_code}: {r.text[:200]}") from e
    data = r.json().get("candles", [])
    rows = []
    for c in data:
        t = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
        o = float(c["mid"]["o"]); h = float(c["mid"]["h"]); l = float(c["mid"]["l"]); cl = float(c["mid"]["c"])
        rows.append({"time": int(t.timestamp()), "open": o, "high": h, "low": l, "close": cl})
    return pd.DataFrame(rows)

def session_ny_am_high(df: pd.DataFrame):
    if df.empty: return None
    times_utc = pd.to_datetime(df["time"], unit="s", utc=True)
    times_ny = times_utc.tz_convert(NY_TZ)
    latest_day = times_ny.dt.date.iloc[-1]
    mask = (times_ny.dt.date == latest_day) & (times_ny.dt.hour >= 8) & (times_ny.dt.hour < 11)
    if not mask.any():
        prev = latest_day - timedelta(days=1)
        mask = (times_ny.dt.date == prev) & (times_ny.dt.hour >= 8) & (times_ny.dt.hour < 11)
    if not mask.any():
        return None
    return float(df.loc[mask, "high"].max())

def detect_fvgs(df: pd.DataFrame, max_keep: int = 12):
    fvgs = []
    for i in range(2, len(df)):
        lo = df["low"].iloc[i]; hi = df["high"].iloc[i]
        lo_2 = df["low"].iloc[i-2]; hi_2 = df["high"].iloc[i-2]
        t0 = int(df["time"].iloc[i-2]); t2 = int(df["time"].iloc[i])
        if lo > hi_2:
            fvgs.append({"type":"bullish","start":t0,"end":t2,"low":float(hi_2),"high":float(lo)})
        elif hi < lo_2:
            fvgs.append({"type":"bearish","start":t0,"end":t2,"low":float(hi),"high":float(lo_2)})
    return fvgs[-max_keep:]

def swing_points(df: pd.DataFrame, left:int=3, right:int=3):
    swings = {"highs": [], "lows": []}
    high = df["high"].values; low = df["low"].values
    t = df["time"].values
    for i in range(left, len(df)-right):
        if high[i] == max(high[i-left:i+right+1]):
            swings["highs"].append({"time": int(t[i]), "price": float(high[i])})
        if low[i] == min(low[i-left:i+right+1]):
            swings["lows"].append({"time": int(t[i]), "price": float(low[i])})
    swings["highs"] = swings["highs"][-10:]
    swings["lows"] = swings["lows"][-10:]
    return swings

def ema(series: pd.Series, n: int):
    return series.ewm(span=n, adjust=False).mean()

def compute_signal_instant(df_1m: pd.DataFrame):
    if len(df_1m) < 25: 
        price = float(df_1m["close"].iloc[-1]) if not df_1m.empty else None
        return {"direction":"None","price":price,"take_profit":None,"stop_loss":None,"risk_reward":None}
    ema20 = ema(df_1m["close"], 20)
    c1 = df_1m.iloc[-1]; c2 = df_1m.iloc[-2]
    price = float(c1["close"])
    bull_engulf = (c1["close"] > c1["open"]) and (c1["open"] <= c2["close"]) and (c1["close"] >= c2["open"]) and (c1["close"] > ema20.iloc[-1])
    bear_engulf = (c1["close"] < c1["open"]) and (c1["open"] >= c2["close"]) and (c1["close"] <= c2["open"]) and (c1["close"] < ema20.iloc[-1])
    rr = 2.0
    if bull_engulf:
        sl = float(min(c1["low"], c2["low"])); tp = float(price + rr*(price - sl))
        return {"direction":"Buy","price":price,"take_profit":tp,"stop_loss":sl,"risk_reward":round((tp-price)/(price-sl),2)}
    if bear_engulf:
        sl = float(max(c1["high"], c2["high"])); tp = float(price - rr*(sl - price))
        return {"direction":"Sell","price":price,"take_profit":tp,"stop_loss":sl,"risk_reward":round((price-tp)/(sl-price),2)}
    return {"direction":"None","price":price,"take_profit":None,"stop_loss":None,"risk_reward":None}

def slice_by_range(df: pd.DataFrame, range_key: str):
    if df.empty: return df
    now = datetime.now(timezone.utc)
    if range_key == "1D":
        since = now - timedelta(days=1)
    elif range_key == "1W":
        since = now - timedelta(weeks=1)
    elif range_key == "1M":
        since = now - timedelta(days=30)
    else:
        return df
    return df[df["time"] >= int(since.timestamp())]

def gen_demo_ohlc(n=400, period_sec=60*15, start=None):
    # Create deterministic synthetic candles for demo
    import math, random
    if start is None:
        start = int(datetime.now(timezone.utc).timestamp()) - n*period_sec
    rng = np.linspace(0, 10*math.pi, n)
    base = 1900 + 20*np.sin(rng) + 5*np.cos(rng*0.5)
    noise = np.random.default_rng(7).normal(0, 1.2, n)
    close = base + noise.cumsum()*0.02
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.random.rand(n)*1.1
    low  = np.minimum(open_, close) - np.random.rand(n)*1.1
    t = [start + i*period_sec for i in range(n)]
    return pd.DataFrame({"time":t,"open":open_,"high":high,"low":low,"close":close})

@app.route('/api/signal')
def api_signal():
    asset = request.args.get("asset", "EUR/USD")
    timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)  # 1m/5m/15m
    range_key = request.args.get("range", DEFAULT_RANGE)          # 1D/1W/1M
    demo = request.args.get("demo", "0") == "1"

    try:
        if demo or not OANDA_API_KEY:
            # demo or no API key → synthetic candles
            period_sec = {"1m":60,"5m":300,"15m":900}.get(timeframe, 900)
            df = gen_demo_ohlc(n=500, period_sec=period_sec)
        else:
            df = fetch_ohlc(asset, timeframe=timeframe, count=1000 if timeframe!="1m" else 1500)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 200

    try:
        df_1m = df if timeframe=="1m" else (gen_demo_ohlc(n=500, period_sec=60) if (demo or not OANDA_API_KEY) else fetch_ohlc(asset, timeframe="1m", count=500))
    except Exception as e:
        df_1m = pd.DataFrame()

    ny_high = session_ny_am_high(df)
    fvgs = detect_fvgs(df)
    swings = swing_points(df)
    sig = compute_signal_instant(df_1m)

    df_zoom = slice_by_range(df, range_key)
    ohlc = df_zoom[["time","open","high","low","close"]].to_dict(orient="records")
    payload = {
        "asset": asset, "timeframe": timeframe, "range": range_key,
        "price": sig.get("price"), "direction": sig.get("direction"),
        "take_profit": sig.get("take_profit"), "stop_loss": sig.get("stop_loss"),
        "risk_reward": sig.get("risk_reward"),
        "ny_am_high": ny_high, "fvgs": fvgs, "swings": swings, "ohlc": ohlc,
        "demo": demo or not bool(OANDA_API_KEY),
    }
    return jsonify(payload)

@app.route('/api/diag')
def diag():
    # Quick environment & fetch test
    info = {
        "oanda_env": OANDA_ENV,
        "has_api_key": bool(OANDA_API_KEY),
        "base_url": OANDA_BASE,
        "instruments": list(INSTRUMENT_MAP.items()),
    }
    # Try a small request if API key present
    if OANDA_API_KEY:
        try:
            url = f"{OANDA_BASE}/v3/instruments/EUR_USD/candles"
            r = requests.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"}, params={"granularity":"M15","count":5,"price":"M"}, timeout=15)
            info["test_status"] = r.status_code
            info["test_ok"] = r.ok
            info["test_err"] = None if r.ok else r.text[:200]
        except Exception as e:
            info["test_status"] = None
            info["test_ok"] = False
            info["test_err"] = f"{type(e).__name__}: {e}"
    else:
        info["test_status"] = None
        info["test_ok"] = False
        info["test_err"] = "No OANDA_API_KEY set"
    return jsonify(info)

@app.route('/')
def root():
    resp = make_response(send_from_directory(BASE_DIR, 'Index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/favicon.ico')
def favicon():
    try:
        return send_from_directory(BASE_DIR, 'favicon.ico')
    except Exception:
        from flask import Response
        return Response(status=204)

@app.route('/api/health')
def health():
    return jsonify({"ok": True, "version": "update4-diag"})
