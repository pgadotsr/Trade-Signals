from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
import time

app = FastAPI()

# --- API Keys & Endpoints ---
METALS_API_KEY = "60b73c22f4da4cdb961fd410b2c57fa4"
METALS_URL = "https://metals-api.com/api/latest"
FOREX_URL = "https://open.er-api.com/v6/latest/USD"  # Free forex API

# --- Asset Lists ---
METALS = {
    "XAU": "Gold",
    "XAG": "Silver",
    "XPT": "Platinum",
    "XPD": "Palladium"
}

FOREX = {
    "EUR": "Euro",
    "GBP": "British Pound",
    "JPY": "Japanese Yen"
}

# --- Cache to avoid hitting API limits ---
cache = {"metals": None, "forex": None, "last_fetch": 0}

def fetch_metal_price(symbol):
    params = {"access_key": METALS_API_KEY, "base": symbol}
    r = requests.get(METALS_URL, params=params)
    data = r.json()
    if "rates" in data:
        return data["rates"].get("USD")
    return None

def fetch_forex_price(symbol):
    r = requests.get(FOREX_URL)
    data = r.json()
    if "rates" in data:
        usd_to_symbol = data["rates"].get(symbol)
        if usd_to_symbol:
            return 1 / usd_to_symbol
    return None

def generate_signal(price):
    buy_price = price * 0.99
    sell_price = price * 1.01
    take_profit = price * 1.02
    return buy_price, sell_price, take_profit

@app.get("/", response_class=HTMLResponse)
def home():
    current_time = time.time()

    # Only fetch from APIs every 60 seconds
    if current_time - cache["last_fetch"] > 60:
        metals_prices = {}
        for symbol in METALS:
            metals_prices[symbol] = fetch_metal_price(symbol)
        
        forex_prices = {}
        for symbol in FOREX:
            forex_prices[symbol] = fetch_forex_price(symbol)

        cache["metals"] = metals_prices
        cache["forex"] = forex_prices
        cache["last_fetch"] = current_time
    else:
        metals_prices = cache["metals"]
        forex_prices = cache["forex"]

    # --- Build HTML Page ---
    html = """
    <html>
    <head>
        <title>Trade Signals</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body { font-family: Arial; background: #111; color: #fff; }
            h1, h2 { text-align: center; }
            .asset { padding: 10px; margin: 5px; background: #222; border-radius: 5px; width: 300px; display: inline-block; }
            p { margin: 3px 0; }
        </style>
    </head>
    <body>
        <h1>Trade Signals</h1>
        <h2>Metals</h2>
    """

    for symbol, name in METALS.items():
        price = metals_prices.get(symbol)
        if price:
            buy, sell, tp = generate_signal(price)
            html += f"""
            <div class='asset'>
                <h3>{name} ({symbol})</h3>
                <p>Price: ${price:.2f}</p>
                <p>Buy: ${buy:.2f}</p>
                <p>Sell: ${sell:.2f}</p>
                <p>Take Profit: ${tp:.2f}</p>
            </div>
            """
        else:
            html += f"<div class='asset'><h3>{name} ({symbol})</h3><p>Error fetching price</p></div>"

    html += "<h2>Forex</h2>"
    for symbol, name in FOREX.items():
        price = forex_prices.get(symbol)
        if price:
            buy, sell, tp = generate_signal(price)
            html += f"""
            <div class='asset'>
                <h3>{name} ({symbol}/USD)</h3>
                <p>Price: ${price:.4f}</p>
                <p>Buy: ${buy:.4f}</p>
                <p>Sell: ${sell:.4f}</p>
                <p>Take Profit: ${tp:.4f}</p>
            </div>
            """
        else:
            html += f"<div class='asset'><h3>{name} ({symbol}/USD)</h3><p>Error fetching price</p></div>"

    html += "</body></html>"
    return html
