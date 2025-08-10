from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
import requests
import os

app = FastAPI()

API_KEY = os.getenv("API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = "https://finnhub.io/api/v1/quote"

ASSETS = {
    "Gold": "GC=F",
    "Silver": "SI=F",
    "GBP/USD": "GBPUSD=X",
    "EUR/USD": "EURUSD=X",
    "Bitcoin": "BTC-USD",
    "Tesla": "TSLA"
}

@app.get("/api/signal")
def get_signal(asset: str = Query(...)):
    if asset not in ASSETS:
        return {"error": "Invalid asset"}

    symbol = ASSETS[asset]
    response = requests.get(f"{BASE_URL}?symbol={symbol}&token={API_KEY}")
    data = response.json()

    price = data.get("c", 0)
    direction = "Buy" if price % 2 > 1 else "Sell"
    take_profit = round(price * (1.02 if direction == "Buy" else 0.98), 2)

    return {
        "asset": asset,
        "price": price,
        "direction": direction,
        "take_profit": take_profit
    }
