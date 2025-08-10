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
    tp = price * 1.02
    return buy, sell, tp

@app.get("/", response_class=HTMLResponse)
def index():
    metals = [
        ("Gold (XAU)", "XAU"),
        ("Silver (XAG)", "XAG"),
        ("Platinum (XPT)", "XPT"),
        ("Palladium (XPD)", "XPD")
    ]

    forex = [
        ("Euro (EUR/USD)", "EUR/USD"),
        ("British Pound (GBP/USD)", "GBP/USD"),
        ("Japanese Yen (JPY/USD)", "JPY/USD")
    ]

    html = """
    <html>
    <head>
        <title>Trade Signals</title>
        <style>
            body { background-color: black; color: white; font-family: Arial; text-align: center; }
            h1 { font-size: 2em; }
            h2 { margin-top: 30px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 15px; margin-top: 20px; }
            .card { background: #222; padding: 15px; border-radius: 10px; }
        </style>
    </head>
    <body>
        <h1>Trade Signals</h1>

        <h2>Metals</h2>
        <div class="grid">
    """

    for name, symbol in metals:
        price = fetch_metal_price(symbol)
        if price:
            buy, sell, tp = calculate_trade_levels(price)
            html += f"<div class='card'><strong>{name}</strong><br>Price: ${price:.2f}<br>Buy: ${buy:.2f}<br>Sell: ${sell:.2f}<br>Take Profit: ${tp:.2f}</div>"
        else:
            html += f"<div class='card'><strong>{name}</strong><br>Error fetching price</div>"

    html += """
        </div>
        <h2>Forex</h2>
        <div class="grid">
    """

    for name, pair in forex:
        price = fetch_forex_price(pair)
        if price:
            buy, sell, tp = calculate_trade_levels(price)
            html += f"<div class='card'><strong>{name}</strong><br>Price: ${price:.4f}<br>Buy: ${buy:.4f}<br>Sell: ${sell:.4f}<br>Take Profit: ${tp:.4f}</div>"
        else:
            html += f"<div class='card'><strong>{name}</strong><br>Error fetching price</div>"

    html += "</div></body></html>"
    return HTMLResponse(content=html)
