from flask import Flask, render_template, jsonify
import pandas as pd
import oandapyV20
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.instruments as instruments
import datetime as dt
import os

app = Flask(__name__)

# ---------------- OANDA SETTINGS ----------------
OANDA_API_KEY = os.environ.get("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")
client = oandapyV20.API(access_token=OANDA_API_KEY)

# Instruments list
INSTRUMENTS = [
    "GBP_USD", "EUR_USD", "USD_JPY",
    "NAS100_USD", "US30_USD", "DE40_EUR", "UK100_GBP", "EU50_EUR"
]

# ---------------- HELPER FUNCTIONS ----------------
def fetch_candles(instrument, granularity="M15", count=50):
    params = {
        "granularity": granularity,
        "count": count,
        "price": "M"
    }
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    data = r.response["candles"]

    df = pd.DataFrame([{
        "time": c["time"],
        "open": float(c["mid"]["o"]),
        "high": float(c["mid"]["h"]),
        "low": float(c["mid"]["l"]),
        "close": float(c["mid"]["c"])
    } for c in data])

    df["time"] = pd.to_datetime(df["time"])
    return df

def trade_signal(df, min_points):
    latest_price = df["close"].iloc[-1]
    recent_high = df["high"].max()
    recent_low = df["low"].min()

    if (recent_high - latest_price) >= min_points:
        return "BUY", latest_price, latest_price + min_points
    elif (latest_price - recent_low) >= min_points:
        return "SELL", latest_price, latest_price - min_points
    else:
        return None, latest_price, None

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html", instruments=INSTRUMENTS)

@app.route("/data/<instrument>/<int:min_points>")
def get_data(instrument, min_points):
    df = fetch_candles(instrument)
    signal, entry, tp = trade_signal(df, min_points)
    return jsonify({
        "time": df["time"].astype(str).tolist(),
        "close": df["close"].tolist(),
        "signal": signal,
        "entry": entry,
        "tp": tp
    })

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
