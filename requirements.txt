import os
from flask import Flask, jsonify, request, render_template_string
from datetime import datetime
# ---- your existing imports for OANDA + pandas etc ----
# from oanda_fetch import fetch_oanda_candles, ema_dir, one_min_confirm, recent_30m_range, compute_tp_sl, MIN_TP, ASSETS

app = Flask(__name__)

CACHE_TTL = 0  # unchanged

# ----------------------------------------
# ---------- full analysis per instrument ----------
def analyze_instrument(oanda_symbol):
    """Return analysis dict used by UI and charts for both rules."""
    df_1h = fetch_oanda_candles(oanda_symbol, "H1", count=200)
    df_15 = fetch_oanda_candles(oanda_symbol, "M15", count=200)
    df_5 = fetch_oanda_candles(oanda_symbol, "M5", count=200)
    df_1 = fetch_oanda_candles(oanda_symbol, "M1", count=200)

    out = {"ok": True, "reason": None, "primary_bias": "UNKNOWN", "ma50": None, "data": {}}

    try:
        if df_1h is not None and len(df_1h) >= 60:
            ma50 = df_1h["close"].rolling(window=50).mean().iloc[-1]
            cur = float(df_1h["close"].iloc[-1])
            out["ma50"] = round(float(ma50), 6)
            diff_pct = (cur - ma50) / ma50 * 100
            out["primary_bias"] = "UP" if diff_pct > 0.5 else "DOWN" if diff_pct < -0.5 else "SIDEWAYS"
    except Exception:
        out["primary_bias"] = "UNKNOWN"

    dir_15 = ema_dir(df_15)
    dir_5 = ema_dir(df_5)
    dir_1 = ema_dir(df_1)
    confirm_1m = one_min_confirm(df_1)

    entry = float(df_1["close"].iloc[-1]) if df_1 is not None and len(df_1) > 0 else None

    agreement = (dir_15 in ("BUY", "SELL") and dir_5 in ("BUY", "SELL") and dir_15 == dir_5)
    agreement_side = dir_15 if agreement else None

    range_30m = recent_30m_range(df_1)

    results = {}
    for min_tp in (10.0, 5.0):
        rule_name = f"min_{int(min_tp)}"
        info = {
            "signal": "NO TRADE",
            "entry": None,
            "tp": None,
            "sl": None,
            "reason": "unknown",
            "dir_15": dir_15,
            "dir_5": dir_5,
            "dir_1": dir_1,
            "confirm_1m": confirm_1m
        }

        if not agreement:
            info["reason"] = "15m and 5m do not agree"
            results[rule_name] = info
            continue
        if not (dir_1 in ("BUY", "SELL")):
            info["reason"] = "1m EMA direction unknown"
            results[rule_name] = info
            continue
        if dir_1 != agreement_side:
            info["reason"] = "1m EMA doesn't match 5m/15m agreement"
            results[rule_name] = info
            continue

        mapped_min = MIN_TP.get(oanda_symbol, min_tp)
        if range_30m < mapped_min:
            info["reason"] = f"Low 30m volatility ({range_30m:.4f}) < required {mapped_min}"
            results[rule_name] = info
            continue

        tp, sl, flag = compute_tp_sl(entry, agreement_side, df_15, oanda_symbol, mapped_min)
        if flag != "ok":
            info["reason"] = flag
            results[rule_name] = info
            continue

        info.update({
            "signal": agreement_side,
            "entry": round(entry, 6),
            "tp": tp,
            "sl": sl,
            "reason": "ok"
        })
        results[rule_name] = info

    chart_series = []
    if df_1 is not None and len(df_1) > 0:
        chart_series = [{"time": str(idx), "close": float(v)} for idx, v in zip(df_1.index.astype(str), df_1["close"].values)]

    out["data"] = {"results": results, "chart": chart_series, "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
    return out


# ---------- API endpoint used by page ----------
@app.route("/api/asset", methods=["GET"])
def api_asset():
    name = request.args.get("asset")
    if not name or name not in ASSETS:
        return jsonify({"error": "unknown asset"}), 400
    sym = ASSETS[name]
    analysis = analyze_instrument(sym)
    return jsonify(analysis)


# ---------- Utility to evaluate whole menu coloring ----------
def evaluate_all_assets():
    menu = {}
    for name, sym in ASSETS.items():
        a = analyze_instrument(sym)
        r10 = a["data"]["results"].get("min_10", {})
        r5 = a["data"]["results"].get("min_5", {})
        ok = (r10.get("reason") == "ok") or (r5.get("reason") == "ok")
        menu[name] = {"trade_available": ok}
    return menu


# ---------- Frontend HTML ----------
PAGE_HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signals — +10 / +5</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
/* your CSS unchanged */
</style>
</head>
<body>
<header>
  <h2>Live Signals (OANDA)</h2>
  <div>Asset: <select id="assetSelect"></select></div>
</header>
<div class="container">
  <div id="menuNote" class="small"></div>
  <div class="card" id="topSection">
    <h3>Priority trades — +10 rule</h3>
    <div id="topInfo" class="small"></div>
    <canvas id="chart10"></canvas>
    <table id="table10">
      <thead><tr><th>TF</th><th>Direction</th><th>Entry</th><th>TP</th><th>SL</th><th>1m Confirm</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div class="card" id="botSection">
    <h3>Secondary trades — +5 rule</h3>
    <div id="botInfo" class="small"></div>
    <canvas id="chart5"></canvas>
    <table id="table5">
      <thead><tr><th>TF</th><th>Direction</th><th>Entry</th><th>TP</th><th>SL</th><th>1m Confirm</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div class="small">Primary bias = 1h MA50. 15m & 5m must agree; 1m confirmation required. Auto-refresh every 5s.</div>
  <div class="small" id="updatedAt"></div>
</div>

<script>
const ASSETS = {{ assets|tojson }};
async function loadAsset(name){
  const resp = await fetch(`/api/asset?asset=${encodeURIComponent(name)}`);
  const j = await resp.json();
  const r10 = j.data.results.min_10;
  const r5 = j.data.results.min_5;
  const chart = j.data.chart || [];
  document.getElementById('updatedAt').innerText = "Updated: " + j.data.updated;
  // update UI functions here (unchanged)
}

async function init(){
  const menuResp = await fetch('/api/menu_status');
  const menuJson = await menuResp.json();
  const sel = document.getElementById('assetSelect');
  sel.innerHTML = '';
  for (const name of Object.keys(ASSETS)){
    const opt = document.createElement('option');
    opt.value = name;
    opt.text = name;
    const ok = menuJson[name] && menuJson[name].trade_available;
    opt.style.color = ok ? 'green' : 'red';
    sel.appendChild(opt);
  }
  const chosen = sel.value;
  await loadAsset(chosen);
  sel.onchange = async ()=> { await loadAsset(sel.value); };
  // auto-refresh every 5s
  setInterval(async ()=>{
    const current = sel.value;
    await loadAsset(current);
    const m = await (await fetch('/api/menu_status')).json();
    for (let i=0;i<sel.options.length;i++){
      const opt = sel.options[i];
      opt.style.color = m[opt.value] && m[opt.value].trade_available ? 'green' : 'red';
    }
  }, 5*1000);
}
window.onload = init;
</script>
</body>
</html>
"""

@app.route("/api/menu_status")
def api_menu_status():
    out = {}
    for name, sym in ASSETS.items():
        try:
            res = analyze_instrument(sym)
            r10 = res["data"]["results"].get("min_10", {})
            r5 = res["data"]["results"].get("min_5", {})
            ok = (r10.get("reason") == "ok") or (r5.get("reason") == "ok")
            out[name] = {"trade_available": ok}
        except Exception:
            out[name] = {"trade_available": False}
    return jsonify(out)

@app.route("/")
def index():
    return render_template_string(PAGE_HTML, assets=ASSETS, cache_ttl=CACHE_TTL)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
