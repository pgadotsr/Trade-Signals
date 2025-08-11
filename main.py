import os
import requests
import statistics
from flask import Flask, render_template_string

# ---------------- CONFIG ----------------
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")  # "practice" or "live"

BASE_URL = "https://api-fxpractice.oanda.com/v3" if OANDA_ENV == "practice" else "https://api-fxtrade.oanda.com/v3"

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}"
}

# ---------------- FLASK APP ----------------
app = Flask(__name__)

# ---------------- HTML TEMPLATE ----------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>OANDA Trade Signals</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #111; color: #fff; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #555; padding: 8px; text-align: center; }
        th { background-color: #222; }
        .buy { color: lightgreen; }
        .sell { color: red; }
        .neutral { color: yellow; }
    </style>
</head>
<body>
    <h1>OANDA Trade Signals</h1>
    <table>
        <tr>
            <th>Instrument</th>
            <th>Trend (1H)</th>
            <th>Trend (15M)</th>
            <th>Trend (5M)</th>
            <th>Confirmation (1M)</th>
            <th>Signal</th>
            <th>Entry</th>
            <th>Take Profit</th>
            <th>Stop Loss</th>
        </tr>
        {% for row in data %}
        <tr>
            <td>{{ row.instrument }}</td>
            <td>{{ row.trend_1h }}</td>
            <td>{{ row.trend_15m }}</td>
            <td>{{ row.trend_5m }}</td>
            <td>{{ row.trend_1m }}</td>
            <td class="{{ row.signal_class }}">{{ row.signal }}</td>
            <td>{{ row.entry }}</td>
            <td>{{ row.take_profit }}</td>
            <td>{{ row.stop_loss }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

# ---------------- FUNCTIONS ----------------
def get_candles(instrument, granularity, count=50):
    url = f"{BASE_URL}/instruments/{instrument}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        return []
    data = r.json().get("candles", [])
    closes = [float(c["mid"]["c"]) for c in data if c["complete"]]
    return closes

def detect_trend(closes):
    if len(closes) < 2:
        return "N/A"
    return "UP" if closes[-1] > closes[0] else "DOWN"

def generate_trade_suggestion(instrument):
    closes_1h = get_candles(instrument, "H1")
    closes_15m = get_candles(instrument, "M15")
    closes_5m = get_candles(instrument, "M5")
    closes_1m = get_candles(instrument, "M1")

    trend_1h = detect_trend(closes_1h)
    trend_15m = detect_trend(closes_15m)
    trend_5m = detect_trend(closes_5m)
    trend_1m = detect_trend(closes_1m)

    entry_price = closes_1m[-1] if closes_1m else None
    signal = "NO TRADE"
    signal_class = "neutral"
    take_profit = stop_loss = "N/A"

    if entry_price:
        volatility = statistics.stdev(closes_15m[-20:]) if len(closes_15m) >= 20 else 0
        min_target = 10
        tp_extension = round(volatility * 2, 2)

        if all(t == "UP" for t in [trend_1h, trend_15m, trend_5m, trend_1m]):
            signal = "BUY"
            signal_class = "buy"
            take_profit = round(entry_price + max(min_target, tp_extension), 2)
            stop_loss = round(entry_price - (min_target / 2), 2)
        elif all(t == "DOWN" for t in [trend_1h, trend_15m, trend_5m, trend_1m]):
            signal = "SELL"
            signal_class = "sell"
            take_profit = round(entry_price - max(min_target, tp_extension), 2)
            stop_loss = round(entry_price + (min_target / 2), 2)

        if signal != "NO TRADE" and abs(take_profit - entry_price) < min_target:
            signal = "NO TRADE"
            signal_class = "neutral"
            take_profit = stop_loss = "N/A"

    return {
        "instrument": instrument,
        "trend_1h": trend_1h,
        "trend_15m": trend_15m,
        "trend_5m": trend_5m,
        "trend_1m": trend_1m,
        "signal": signal,
        "signal_class": signal_class,
        "entry": round(entry_price, 2) if entry_price else "N/A",
        "take_profit": take_profit,
        "stop_loss": stop_loss
    }

@app.route("/")
def index():
    instruments = ["XAU_USD", "EUR_USD", "GBP_USD"]  # Add more symbols if you want
    data = [generate_trade_suggestion(instr) for instr in instruments]
    return render_template_string(HTML_TEMPLATE, data=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
