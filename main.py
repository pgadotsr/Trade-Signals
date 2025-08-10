from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import requests
import os

app = FastAPI()

API_KEY = "60b73c22f4da4cdb961fd410b2c57fa4"  # Your Metals API key
BASE_URL = "https://metals-api.com/api/latest"

ASSETS = {
    "Gold": "XAU",
    "Silver": "XAG",
    "GBP/USD": "GBP",
    "EUR/USD": "EUR"
}

# Serve HTML homepage
@app.get("/", response_class=HTMLResponse)
def home():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trade Signals</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f5f5f5;
                text-align: center;
                padding: 20px;
            }
            select, button {
                font-size: 16px;
                padding: 10px;
                margin: 5px;
            }
            #result {
                margin-top: 20px;
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 0 5px rgba(0,0,0,0.2);
                display: inline-block;
                min-width: 200px;
            }
        </style>
    </head>
    <body>
        <h1>📊 Trade Signal Dashboard</h1>
        <select id="asset">
            <option value="Gold">Gold</option>
            <option value="Silver">Silver</option>
            <option value="GBP/USD">GBP/USD</option>
            <option value="EUR/USD">EUR/USD</option>
        </select>
        <button onclick="getSignal()">Get Signal</button>

        <div id="result">Select an asset to view signal.</div>

        <script>
            async function getSignal() {
                const asset = document.getElementById('asset').value;
                const res = await fetch(`/api/signal?asset=${encodeURIComponent(asset)}`);
                const data = await res.json();

                if (data.error) {
                    document.getElementById('result').innerHTML = `<b>Error:</b> ${data.error}`;
                    return;
                }

                document.getElementById('result').innerHTML = `
                    <h2>${data.asset}</h2>
                    <p>Price: $${data.price}</p>
                    <p>Direction: <b>${data.direction}</b></p>
                    <p>Take Profit: $${data.take_profit}</p>
                `;
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# API endpoint for signals
@app.get("/api/signal")
def get_signal(asset: str = Query(...)):
    if asset not in ASSETS:
        return JSONResponse(content={"error": "Invalid asset"}, status_code=400)

    symbol = ASSETS[asset]
    params = {
        "access_key": API_KEY,
        "base": "USD",
        "symbols": symbol
    }
    response = requests.get(BASE_URL, params=params)
    data = response.json()

    price = data.get("rates", {}).get(symbol)
    if price is None:
        return JSONResponse(content={"error": "Price not found"}, status_code=500)

    direction = "BUY" if int(price) % 2 == 0 else "SELL"
    take_profit = round(price * (1.01 if direction == "BUY" else 0.99), 2)

    return {
        "asset": asset,
        "price": price,
        "direction": direction,
        "take_profit": take_profit
    }
