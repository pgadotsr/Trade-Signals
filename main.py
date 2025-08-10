import os
import requests
import pandas as pd
import numpy as np
from flask import Flask, jsonify
import ta  # Technical Analysis library

# Get OANDA credentials from environment
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_TYPE = os.getenv("OANDA_ENV", "practice")  # "practice" or "live"

# OANDA API URL
OANDA_URL = f"https://api-fxpractice.oanda.com/v3/instruments"

# Instruments to track
INSTRUMENTS = ["XAU_USD", "EUR_USD", "GBP_USD"]

# Flask app
app = Flask(__name__)

def fetch_candles(instrument, granularity="M5", count=100):
    """Fetch recent candle data from OANDA."""
    url = f"https://api-fxpractice.oanda.com/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    params = {
        "granularity": granularity,
        "count": count,
        "price": "M"
    }
    r = requests.get(url, headers=headers, params=params)
    data = r.json()

    if "candles" not in data:
        return None

    df = pd.DataFrame([{
        "time": c["time"],
        "open": float(c["mid"]["o"]),
        "high": float(c["mid"]["h"]),
        "low": float(c["mid"]["l"]),
        "close": float(c["mid"]["c"])
    } for c in data["candles"] if c["complete"]])
    
    return df

def analyze(df):
    """Run technical analysis and return signal + targets."""
    if df is None or df.empty:
        return {"signal": "N/A", "tp": None, "sl": None}

    # EMA Strategy
    df["EMA20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["EMA50"] = ta.trend.ema_indicator(df["close"], window=50)

    # RSI
    df["RSI"] = ta.momentum.rsi(df["close"], window=14)

    # MACD
    macd = ta.trend.macd_diff(df["close"])
    df["MACD"] = macd

    latest = df.iloc[-1]
    close = latest["close"]

    # Signal logic
    if latest["EMA20"] > latest["EMA50"] and latest["RSI"] > 50 and latest["MACD"] > 0:
        signal = "BUY"
        tp = round(close * 1.002, 4)  # Take Profit ~0.2% above
        sl = round(close * 0.998, 4)  # Stop Loss ~0.2% below
    elif latest["EMA20"] < latest["EMA50"] and latest["RSI"] < 50 and latest["MACD"] < 0:
        signal = "SELL"
        tp = round(close * 0.998, 4)  # Take Profit ~0.2% below
        sl = round(close * 1.002, 4)  # Stop Loss ~0.2% above
    else:
        signal = "HOLD"
        tp = None
        sl = None

    return {
        "signal": signal,
        "price": close,
        "tp": tp,
        "sl": sl,
        "RSI": round(latest["RSI"], 2),
        "EMA20": round(latest["EMA20"], 4),
        "EMA50": round(latest["EMA50"], 4),
        "MACD": round(latest["MACD"], 5)
    }

@app.route("/")
def index():
    results = {}
    for instrument in INSTRUMENTS:
        df = fetch_candles(instrument, granularity="M5", count=100)
        results[instrument] = analyze(df)
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
