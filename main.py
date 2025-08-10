from flask import Flask, request
import requests
import pandas as pd
import numpy as np
import ta

app = Flask(__name__)

API_KEY = "A25IELIDXARY4KIX"

# Asset symbols for Alpha Vantage
ASSETS = {
    "Gold (XAU/USD)": "XAUUSD",
    "Silver (XAG/USD)": "XAGUSD",
    "Oil (WTI/USD)": "WTIUSD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD"
}

def get_candles(symbol, interval, outputsize="compact"):
    url = f"https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": symbol[:3],
        "to_symbol": symbol[3:],
        "interval": interval,
        "apikey": API_KEY,
        "outputsize": outputsize
    }
    r = requests.get(url)
    data = r.json()
    key = f"Time Series FX ({interval})"
    if key not in data:
        return None
    df = pd.DataFrame(data[key]).T
    df.columns = ["open", "high", "low", "close"]
    df = df.astype(float)
    return df.iloc[::-1]  # Oldest first

def analyze(symbol):
    # Fetch multiple timeframes
    df_1h = get_candles(symbol, "60min", "full")
    df_15m = get_candles(symbol, "15min", "full")
    df_5m = get_candles(symbol, "5min", "full")
    df_1m = get_candles(symbol, "1min", "full")

    if None in (df_1h, df_15m, df_5m, df_1m):
        return {"error": "Data fetch error"}

    # Determine trends
    def trend(df):
        return "up" if df["close"].iloc[-1] > df["close"].mean() else "down"

    trend_1h = trend(df_1h)
    trend_15m = trend(df_15m)
    trend_5m = trend(df_5m)
    trend_1m = trend(df_1m)

    # ATR filter on 15m
    atr = ta.volatility.AverageTrueRange(
        high=df_15m["high"], low=df_15m["low"], close=df_15m["close"], window=14
    ).average_true_range().iloc[-1]

    # Support/Resistance check
    recent_high = df_15m["high"].tail(50).max()
    recent_low = df_15m["low"].tail(50).min()

    signal = "No Trade"
    tp = None
    sl = None

    if trend_1h == trend_15m == trend_5m:
        if trend_1m == trend_1h:
            signal = "BUY" if trend_1h == "up" else "SELL"
            last_price = df_1m["close"].iloc[-1]
            if signal == "BUY":
                tp = last_price + atr * 2
                sl = last_price - atr
            else:
                tp = last_price - atr * 2
                sl = last_price + atr

    return {
        "trend_1h": trend_1h,
        "trend_15m": trend_15m,
        "trend_5m": trend_5m,
        "trend_1m": trend_1m,
        "ATR": round(atr, 2),
        "support": recent_low,
        "resistance": recent_high,
        "signal": signal,
        "tp": tp,
        "sl": sl
    }

@app.route("/", methods=["GET"])
def home():
    asset_name = request.args.get("asset", "Gold (XAU/USD)")
    symbol = ASSETS[asset_name]
    analysis = analyze(symbol)

    html = """
    <html>
    <head>
    <meta http-equiv="refresh" content="60">
    <style>
        body { font-family: Arial; max-width: 600px; margin: auto; padding: 20px; }
        h1 { text-align: center; }
        .signal { font-size: 1.5em; font-weight: bold; padding: 10px; border-radius: 5px; }
        .buy { background-color: #d4edda; color: #155724; }
        .sell { background-color: #f8d7da; color: #721c24; }
        .neutral { background-color: #e2e3e5; color: #383d41; }
        select { font-size: 1em; padding: 5px; }
    </style>
    </head>
    <body>
        <h1>Trading Dashboard</h1>
        <form method="get">
            <select name="asset" onchange="this.form.submit()">
    """
    for name in ASSETS:
        selected = "selected" if name == asset_name else ""
        html += f'<option {selected}>{name}</option>'
    html += """
            </select>
        </form>
    """

    if "error" in analysis:
        html += f"<p>{analysis['error']}</p>"
    else:
        sig_class = "neutral"
        if analysis["signal"] == "BUY":
            sig_class = "buy"
        elif analysis["signal"] == "SELL":
            sig_class = "sell"

        html += f"""
        <p>1H Trend: {analysis['trend_1h']}</p>
        <p>15M Trend: {analysis['trend_15m']}</p>
        <p>5M Trend: {analysis['trend_5m']}</p>
        <p>1M Confirmation: {analysis['trend_1m']}</p>
        <p>ATR (15M): {analysis['ATR']}</p>
        <p>Support: {analysis['support']}</p>
        <p>Resistance: {analysis['resistance']}</p>
        <div class="signal {sig_class}">Signal: {analysis['signal']}</div>
        <p>Take Profit: {analysis['tp']}</p>
        <p>Stop Loss: {analysis['sl']}</p>
        """
    html += "</body></html>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
