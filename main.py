# main.py
import os
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify
import pandas as pd
import numpy as np

app = Flask(__name__)

# ---------- Config ----------
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")  # "live" or "practice"
OANDA_BASE = "https://api-fxtrade.oanda.com" if OANDA_ENV == "live" else "https://api-fxpractice.oanda.com"

# UI defaults
DEFAULT_TIMEFRAME = "15m"  # per your request
DEFAULT_RANGE = "1D"       # chart zoom default

# Asset -> OANDA instrument mapping (adjust as needed)
INSTRUMENT_MAP = {
    "Gold": "XAU_USD",
    "Silver": "XAG_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "Bitcoin": "BTC_USD",     # available on some OANDA accounts
    "Tesla": "TSLA_USD",      # may not be enabled; swap to your data source if needed
}

GRANULARITY_MAP = {"1m": "M1", "5m": "M5", "15m": "M15"}
NY_TZ = ZoneInfo("America/New_York")

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
    params = {"granularity": gran, "count": count, "price": "M"}  # mid
    r = requests.get(url, headers=oanda_headers(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("candles", [])
    rows = []
    for c in data:
        t = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
        o = float(c["mid"]["o"]); h = float(c["mid"]["h"]); l = float(c["mid"]["l"]); cl = float(c["mid"]["c"])
        rows.append({"time": int(t.timestamp()), "open": o, "high": h, "low": l, "close": cl})
    df = pd.DataFrame(rows)
    return df

def session_ny_am_high(df: pd.DataFrame) -> float | None:
    # NY AM = 08:00–11:00 America/New_York of the *latest trading day in df*
    if df.empty: return None
    # map to NY times
    times_utc = pd.to_datetime(df["time"], unit="s", utc=True)
    times_ny = times_utc.tz_convert(NY_TZ)
    latest_day = times_ny.dt.date.iloc[-1]
    mask = (times_ny.dt.date == latest_day) & (times_ny.dt.hour >= 8) & (times_ny.dt.hour < 11)
    if not mask.any():  # fall back to same windows of previous day
        # try previous date
        prev = latest_day - timedelta(days=1)
        mask = (times_ny.dt.date == prev) & (times_ny.dt.hour >= 8) & (times_ny.dt.hour < 11)
    if not mask.any():
        return None
    return float(df.loc[mask, "high"].max())

def detect_fvgs(df: pd.DataFrame, max_keep: int = 12):
    # 3-candle FVG definition
    # Bullish FVG if low[n] > high[n-2]; gap = [high[n-2], low[n]]
    # Bearish FVG if high[n] < low[n-2]; gap = [high[n], low[n-2]]
    fvgs = []
    for i in range(2, len(df)):
        lo = df["low"].iloc[i]; hi = df["high"].iloc[i]
        lo_2 = df["low"].iloc[i-2]; hi_2 = df["high"].iloc[i-2]
        t0 = int(df["time"].iloc[i-2]); t2 = int(df["time"].iloc[i])
        if df["low"].iloc[i] > df["high"].iloc[i-2]:
            fvgs.append({"type":"bullish","start":t0,"end":t2,"low":float(hi_2),"high":float(lo)})
        elif df["high"].iloc[i] < df["low"].iloc[i-2]:
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
    # only keep the latest handful to avoid clutter
    swings["highs"] = swings["highs"][-10:]
    swings["lows"] = swings["lows"][-10:]
    return swings

def ema(series: pd.Series, n: int):
    return series.ewm(span=n, adjust=False).mean()

def compute_signal_instant(df_1m: pd.DataFrame):
    # Simple instant-entry 1x1m: engulfing vs EMA(20)
    # Return direction, tp, sl
    if len(df_1m) < 25: 
        price = float(df_1m["close"].iloc[-1]) if not df_1m.empty else None
        return {"direction":"None","price":price,"take_profit":None,"stop_loss":None,"risk_reward":None}
    ema20 = ema(df_1m["close"], 20)
    c1 = df_1m.iloc[-1]; c2 = df_1m.iloc[-2]
    price = float(c1["close"])
    bull_engulf = (c1["close"] > c1["open"]) and (c1["open"] <= c2["close"]) and (c1["close"] >= c2["open"]) and (c1["close"] > ema20.iloc[-1])
    bear_engulf = (c1["close"] < c1["open"]) and (c1["open"] >= c2["close"]) and (c1["close"] <= c2["open"]) and (c1["close"] < ema20.iloc[-1])
    # TP/SL via ATR(14) proxy
    tr = pd.concat([df_1m["high"]-df_1m["low"], 
                    (df_1m["high"]-df_1m["close"].shift()).abs(),
                    (df_1m["low"]-df_1m["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    if pd.isna(atr) or atr == 0: atr = (df_1m["high"].iloc[-14:]-df_1m["low"].iloc[-14:]).mean()
    rr = 2.0
    if bull_engulf:
        sl = float(min(c1["low"], c2["low"]))
        tp = float(price + rr*(price - sl))
        return {"direction":"Buy","price":price,"take_profit":tp,"stop_loss":sl,"risk_reward":round((tp-price)/(price-sl),2)}
    if bear_engulf:
        sl = float(max(c1["high"], c2["high"]))
        tp = float(price - rr*(sl - price))
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

@app.route('/api/signal')
def api_signal():
    asset = request.args.get("asset", "EUR/USD")
    timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)  # 1m/5m/15m
    range_key = request.args.get("range", DEFAULT_RANGE)          # 1D/1W/1M

    # fetch selected TF candles for chart
    try:
        df = fetch_ohlc(asset, timeframe=timeframe, count=1500 if timeframe=="1m" else 1000)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # also fetch 1m for instant-entry logic (independent of chart TF)
    try:
        df_1m = df if timeframe=="1m" else fetch_ohlc(asset, timeframe="1m", count=500)
    except Exception as e:
        df_1m = pd.DataFrame()

    # components
    ny_high = session_ny_am_high(df)
    fvgs = detect_fvgs(df)
    swings = swing_points(df)
    sig = compute_signal_instant(df_1m)

    # subset for chart zoom range
    df_zoom = slice_by_range(df, range_key)

    ohlc = df_zoom[["time","open","high","low","close"]].to_dict(orient="records")
    payload = {
        "asset": asset,
        "timeframe": timeframe,
        "range": range_key,
        "price": sig.get("price"),
        "direction": sig.get("direction"),
        "take_profit": sig.get("take_profit"),
        "stop_loss": sig.get("stop_loss"),
        "risk_reward": sig.get("risk_reward"),
        "ny_am_high": ny_high,
        "fvgs": fvgs,
        "swings": swings,
        "ohlc": ohlc
    }
    return jsonify(payload)

@app.route('/')
def root():
    return jsonify({"ok": True, "msg": "Backend running. Use /api/signal?asset=EUR/USD&timeframe=15m&range=1W"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", "8000")), debug=False)
