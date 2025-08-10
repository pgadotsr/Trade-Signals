# main.py
# Flask app: combined 1m/5m/15m table with ATR-based TP/SL and 1h bias
# Requires: flask, requests, pandas, numpy, ta

from flask import Flask, request, render_template_string
import requests, time
import pandas as pd
import numpy as np
import ta

app = Flask(__name__)

ALPHA_KEY = "A25IELIDXARY4KIX"  # your Alpha Vantage key
CACHE = {}
CACHE_TTL = 55  # seconds

# Assets supported (AlphaVantage FX style symbols)
ASSETS = {
    "Gold (XAU/USD)": "XAUUSD",
    "Silver (XAG/USD)": "XAGUSD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY"
}

# HTML template (simple mobile friendly)
HTML = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>Combined Multi-TF Signals</title>
  <style>
    body{font-family:Arial;margin:12px;color:#111}
    table{width:100%;border-collapse:collapse}
    th,td{padding:8px;border:1px solid #ddd;text-align:center;font-size:14px}
    th{background:#f4f4f4}
    .buy{background:#e6ffed}
    .se
