from flask import Flask, render_template_string, request
import requests
import pandas as pd
import numpy as np
import ta

app = Flask(__name__)

API_KEY = "A25IELIDXARY4KIX"

ASSETS = {
    "Gold (XAU/USD)": "XAUUSD",
    "Silver (XAG/USD)": "XAGUSD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "Oil (WTI)": "WTIUSD"
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Trading Signal Dashboard</title>
    <meta http-equiv="refresh" content="60">
    <style>
        body { font-family: Arial; text-align: center; }
        h1 { color: #333; }
        select { font-size: 16px; padding: 5px; }
        .signal { font-size: 24px; font-weight: bold; margin-top: 20px; }
        .tp-sl { font-size: 28px; font-weight: bold; margin-top: 10px; color: darkblue; }
        .notes { font-size: 18px; margin-top: 15px; color: gray; }
    </style>
</head>
<body>
    <h1>📊 Trading Signal Dashboard</h1>
    <form method="get">
        <label for="asset">Choose Asset:</label>
        <select name="asset" id="asset" onchange="this.form.submit()">
            {% for name in assets %}
                <option value="{{ name }}" {% if name == selected_asset %}selected{% endif %}>{{ name }}</option>
            {% endfor %}
        </select>
    </form>
    <div class="signal">{{ signal }}</div>
    <div class="tp-sl">TP: {{ tp }} | SL: {{ sl }}</div>
    <div class="notes">{{ notes }}</div>
    <p>Last updated: {{ updated }}</p>
</body>
</html>
"""

def get_data(symbol, interval):
    url = f"https://www.alphavantage.co/query?function=FX_INTRADAY&from_symbol={symbol[:3]}&to_symbol={symbol[3:]}&interval={interval}min&apikey={API_KEY}&outputsize=compact"
    r = requests.get(url)
    data = r.json()
    if "Time Series FX" not in data:
        return None
    df = pd.DataFrame(data[f"Time Series FX ({interval}min)"]).T
    df = df.rename(columns={
        "1. open": "open",
        "2. high": "high",
        "3. low": "low",
        "4. close": "close"
    }).astype(float)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()

def atr_filter(df):
    atr =
