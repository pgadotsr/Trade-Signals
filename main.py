from flask import Flask, render_template
import pandas as pd
import oandapyV20
import oandapyV20.endpoints.pricing as pricing
import oandapyV20.endpoints.instruments as instruments
import os

app = Flask(__name__)

# OANDA API Setup (environment variables)
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
client = oandapyV20.API(access_token=OANDA_API_KEY)

# Instruments to display
INSTRUMENTS = [
    "GBP_USD", "EUR_USD", "USD_JPY", "AUD_USD",
    "NAS100_USD", "US30_USD", "DE40_EUR", "UK100_GBP", "EU50_EUR"
]

def fetch_candles(instrument, granularity="M15", count=100):
    params = {"granularity": granularity, "count": count, "price": "M"}
    r = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    candles = r.response.get("candles", [])
    df = pd.DataFrame([
        {
            "time": c["time"],
            "mid_o": float(c["mid"]["o"]),
            "mid_h": float(c["mid"]["h"]),
            "mid_l": float(c["mid"]["l"]),
            "mid_c": float(c["mid"]["c"])
        }
        for c in candles if c["complete"]
    ])
    return df

def check_trade_signal(df, points_target):
    high = df["mid_h"].max()
    low = df["mid_l"].min()
    last_price = df["mid_c"].iloc[-1]
    if high - last_price >= points_target:
        return "Buy"
    elif last_price - low >= points_target:
        return "Sell"
    return None

@app.route("/")
def index():
    data = []
    for instrument in INSTRUMENTS:
        df = fetch_candles(instrument)
        signal_10 = check_trade_signal(df, points_target=10)
        signal_5 = check_trade_signal(df, points_target=5)

        color = "green" if signal_10 or signal_5 else "red"

        data.append({
            "instrument": instrument,
            "signal_10": signal_10,
            "signal_5": signal_5,
            "color": color
        })

    return render_template("index.html", data=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
