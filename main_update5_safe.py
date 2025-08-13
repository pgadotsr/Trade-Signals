
from flask import Flask, request, jsonify, send_from_directory
import pandas as pd
import numpy as np
import time
import os
import traceback

app = Flask(__name__)

@app.route('/')
def index():
    return send_from_directory('.', 'Index.html')

@app.route('/api/health')
def health():
    return jsonify({"ok": True, "version": "update5-safe"})

@app.route('/api/signal')
def signal():
    try:
        # Query parameters
        asset = request.args.get('asset', 'GBP/USD')
        timeframe = request.args.get('timeframe', '15m')
        rng = request.args.get('range', '1D')
        demo = request.args.get('demo', '0') == '1'

        if demo:
            # Always generate ~600 demo candles
            now = int(time.time())
            periods = 600
            step = 900 if timeframe.endswith('m') else 3600
            times = [now - i * step for i in range(periods)][::-1]

            prices = np.cumsum(np.random.randn(periods)) + 100
            highs = prices + np.random.rand(periods)
            lows = prices - np.random.rand(periods)
            opens = prices + (np.random.rand(periods) - 0.5)
            closes = prices + (np.random.rand(periods) - 0.5)

            ohlc = [
                {
                    "time": int(t),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c)
                }
                for t, o, h, l, c in zip(times, opens, highs, lows, closes)
            ]

            return jsonify({
                "asset": asset,
                "timeframe": timeframe,
                "range": rng,
                "count": len(ohlc),
                "ohlc": ohlc
            })

        # Live mode placeholder - currently not implemented
        return jsonify({
            "asset": asset,
            "timeframe": timeframe,
            "range": rng,
            "count": 0,
            "ohlc": [],
            "error": "Live mode not implemented in safe build"
        })

    except Exception as e:
        return jsonify({
            "error": str(e),
            "trace": traceback.format_exc()
        }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
