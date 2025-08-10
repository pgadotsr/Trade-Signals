import requests
import os

# Put your Alpha Vantage key here
API_KEY = "A25IELIDXARY4KIX"  # replace if different

# Choose the asset symbol
symbol = "EURUSD"  # Can be "XAUUSD" for gold

# Choose the timeframe you want to test
timeframes = {
    "1m": "TIME_SERIES_INTRADAY&interval=1min",
    "5m": "TIME_SERIES_INTRADAY&interval=5min",
    "15m": "TIME_SERIES_INTRADAY&interval=15min",
    "1h": "TIME_SERIES_INTRADAY&interval=60min"
}

def fetch_data(tf_name, tf_url_part):
    url = f"https://www.alphavantage.co/query?function={tf_url_part}&symbol={symbol}&apikey={API_KEY}&outputsize=compact"
    r = requests.get(url)
    if r.status_code != 200:
        print(f"[{tf_name}] HTTP error:", r.status_code)
        return
    data = r.json()
    # Find the time series key dynamically
    ts_key = next((k for k in data.keys() if "Time Series" in k), None)
    if not ts_key:
        print(f"[{tf_name}] No time series data found. Full response:\n", data)
        return
    candles = list(data[ts_key].items())
    print(f"\n--- {tf_name} Latest 3 candles ---")
    for time, ohlcv in candles[:3]:
        print(time, ohlcv)

for tf_name, tf_url in timeframes.items():
    fetch_data(tf_name, tf_url)
