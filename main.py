from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests

app = FastAPI()

# --- API Keys ---
METALS_API_KEY = "60b73c22f4da4cdb961fd410b2c57fa4"

# --- API URLs ---
METALS_URL = "https://metals-api.com/api/latest"
FOREX_URL = "https://open.er-api.com/v6/latest/USD"

# --- Fetch Metals Prices ---
def fetch_metal_price(symbol):
    params = {
        "access_key": METALS_API_KEY,
        "base": "USD",
        "symbols": symbol
    }
    r = requests.get(METALS_URL, params=params)
    data = r.json()
    if "rates" in data and symbol in data["rates"]:
        return 1 / data["rates"][symbol]  # Convert USD -> metal price
    return None

# --- Fetch Forex Prices ---
def fetch_forex_price(pair):
    r = requests.get(FOREX_URL)
    data = r.json()
    if "rates" in data:
        base, quote = pair.split("/")
        if quote in data["rates"]:
            return 1 / data["rates"][quote]  # USD -> base currency
    return None

# --- Calculate Buy/Sell/TP ---
def calculate_trade_levels(price):
    buy = price * 0.985
    sell = price * 1.01
    tp = price
