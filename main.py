import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import oandapyV20
import oandapyV20.endpoints.instruments as instruments
from datetime import datetime, timedelta
import pytz
import time
import os

# ===================
# CONFIG
# ===================
OANDA_API_KEY = os.getenv("OANDA_API_KEY")  # Put your key in environment variables
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENV = "practice"  # or "live"
API = oandapyV20.API(access_token=OANDA_API_KEY)

REFRESH_INTERVAL = 5000  # 5 seconds

ASSETS = [
    "XAU_USD", "GBP_USD", "EUR_USD", "USD_JPY",
    "NAS100_USD", "US30_USD", "GER40_EUR", "UK100_GBP", "EU50_EUR"
]

TIMEFRAMES = {"1m": "M1", "5m": "M5", "15m": "M15"}


# ===================
# FUNCTIONS
# ===================
def fetch_candles(symbol, granularity, count=200):
    params = {
        "granularity": granularity,
        "count": count,
        "price": "M"
    }
    r = instruments.InstrumentsCandles(instrument=symbol, params=params)
    API.request(r)
    candles = r.response["candles"]
    df = pd.DataFrame([{
        "time": c["time"],
        "open": float(c["mid"]["o"]),
        "high": float(c["mid"]["h"]),
        "low": float(c["mid"]["l"]),
        "close": float(c["mid"]["c"])
    } for c in candles])
    df["time"] = pd.to_datetime(df["time"])
    return df


def generate_signal(df, points):
    """Simple rule: TP = entry + points, SL = entry - points"""
    last_price = df["close"].iloc[-1]
    entry = last_price
    tp = entry + points
    sl = entry - points
    return {"entry": entry, "tp": tp, "sl": sl}


def plot_chart(df, signal):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["time"], y=df["close"], mode="lines", name="Price", line=dict(color="blue")))
    fig.add_hline(y=signal["tp"], line=dict(color="green", dash="dash"))
    fig.add_hline(y=signal["sl"], line=dict(color="red", dash="dash"))
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=0, b=0))
    return fig


# ===================
# STREAMLIT UI
# ===================
st.set_page_config(page_title="Trade Signals", layout="centered")

st.title("Signals — Combined 1m / 5m / 15m")
st.caption("Primary bias = 1h MA50. Signals require 15m & 5m agreement to propose a trade; 1m is confirmation. Auto-updates every 5s.")

asset = st.selectbox("Asset:", ASSETS)
st.caption(f"Last updated: {datetime.utcnow().isoformat()}Z")

for points, label in [(10, "+10 Point Rule"), (5, "+5 Point Rule")]:
    st.subheader(f"{label}")
    try:
        df = fetch_candles(asset, "M1", 200)
        signal = generate_signal(df, points)
        st.plotly_chart(plot_chart(df, signal))
        st.table(pd.DataFrame([{
            "TF": tf,
            "Direction": "BUY",
            "Entry": signal["entry"],
            "TP": signal["tp"],
            "SL": signal["sl"]
        } for tf in TIMEFRAMES.keys()]))
    except Exception as e:
        st.error(f"Error: {e}")

# Auto refresh
st_autorefresh = st.experimental_rerun
time.sleep(REFRESH_INTERVAL / 1000)
st_autorefresh()
