import os
import time
import threading
import requests
from collections import deque
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

# ==========================
# CONFIG
# ==========================
BASE_URL = "https://api.invertironline.com"
LOGIN_URL = f"{BASE_URL}/token"
QUOTE_URL = f"{BASE_URL}/api/v2/bCBA/Titulos"

SYMBOLS = ["AL30", "GD30"]

REFRESH = int(os.getenv("REFRESH", 5))
HISTORY_LEN = 20
CANDLE_SECONDS = 60

USERNAME = os.getenv("IOL_USER")
PASSWORD = os.getenv("IOL_PASS")

# ==========================
# STATE
# ==========================
token = None
token_expiry = 0

history = {s: deque(maxlen=HISTORY_LEN) for s in SYMBOLS}
last_signals = {}

candles = {s: [] for s in SYMBOLS}
current_candle = {}

bot_running = False

# ==========================
# AUTH
# ==========================
def login():
    global token, token_expiry

    for i in range(3):
        try:
            print(f"[LOGIN TRY {i}]", flush=True)

            r = requests.post(
                LOGIN_URL,
                data={
                    "username": USERNAME,
                    "password": PASSWORD,
                    "grant_type": "password"
                },
                timeout=10
            )

            if r.status_code == 200:
                data = r.json()
                token = data["access_token"]
                token_expiry = time.time() + data["expires_in"] - 60
                print("[LOGIN OK]", flush=True)
                return

        except Exception as e:
            print("[LOGIN ERROR]", e, flush=True)

        time.sleep(2)

    print("[LOGIN FAILED]", flush=True)

def get_headers():
    global token

    if token is None or time.time() > token_expiry:
        login()

    return {"Authorization": f"Bearer {token}"}

# ==========================
# DATA
# ==========================
def get_quote(symbol):
    try:
        url = f"{QUOTE_URL}/{symbol}/Cotizacion"
        r = requests.get(url, headers=get_headers(), timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()
        price = data.get("ultimoPrecio")
        puntas = data.get("puntas", [])

        if price is None:
            return None

        if not puntas:
            return {"price": price, "bid": 0, "ask": 0}

        p = puntas[0]

        return {
            "price": price,
            "bid": p.get("cantidadCompra", 0),
            "ask": p.get("cantidadVenta", 0)
        }

    except Exception as e:
        print("[ERROR]", e, flush=True)
        return None

# ==========================
# VELAS
# ==========================
def update_candle(symbol, price):
    now = int(time.time())
    bucket = now - (now % CANDLE_SECONDS)

    if symbol not in current_candle:
        current_candle[symbol] = {
            "time": bucket,
            "open": price,
            "high": price,
            "low": price,
            "close": price
        }
        return

    c = current_candle[symbol]

    if c["time"] == bucket:
        c["high"] = max(c["high"], price)
        c["low"] = min(c["low"], price)
        c["close"] = price
    else:
        candles[symbol].append(c.copy())

        if len(candles[symbol]) > 200:
            candles[symbol].pop(0)

        current_candle[symbol] = {
            "time": bucket,
            "open": price,
            "high": price,
            "low": price,
            "close": price
        }

# ==========================
# SIGNAL
# ==========================
def compute_signal(hist):
    if len(hist) < 2:
        return "INIT"

    last = hist[-1]
    prev = hist[-2]

    if last["bid"] == 0 and last["ask"] == 0:
        return "SIN MERCADO"

    if last["ask"] > 0 and last["bid"] / max(last["ask"], 1) > 10:
        return "BUY"

    if last["price"] > prev["price"]:
        return "UP"

    if last["price"] < prev["price"]:
        return "DOWN"

    return "FLAT"

# ==========================
# BOT LOOP
# ==========================
def bot_loop():
    print("[BOT STARTED]", flush=True)

    while True:
        try:
            for s in SYMBOLS:
                d = get_quote(s)

                if d is None:
                    continue

                history[s].append(d)

                # 🔥 UPDATE VELAS
                update_candle(s, d["price"])

                signal = compute_signal(history[s])

                last_signals[s] = {
                    "price": d["price"],
                    "signal": signal,
                    "time": datetime.now().strftime("%H:%M:%S")
                }

                print(f"[{s}] {d['price']} {signal}", flush=True)

            time.sleep(REFRESH)

        except Exception as e:
            print("[LOOP ERROR]", e, flush=True)
            time.sleep(5)

# ==========================
# API
# ==========================
app = FastAPI()

@app.on_event("startup")
def startup():
    global bot_running

    print("[STARTUP]", flush=True)

    if not bot_running:
        t = threading.Thread(target=bot_loop, daemon=True)
        t.start()
        bot_running = True

@app.get("/data")
def data():
    return JSONResponse(content=last_signals)

@app.get("/candles")
def get_candles():
    return JSONResponse(content=candles)

# ==========================
# DASHBOARD CON GRAFICO
# ==========================
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
    <h1>📊 BOT EN VIVO</h1>
    <canvas id="chart"></canvas>

    <script>
    async function load() {
        const res = await fetch('/candles');
        const data = await res.json();

        const symbol = Object.keys(data)[0];
        const candles = data[symbol];

        const labels = candles.map(c => new Date(c.time * 1000).toLocaleTimeString());
        const prices = candles.map(c => c.close);

        new Chart(document.getElementById('chart'), {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: symbol,
                    data: prices
                }]
            }
        });
    }

    load();
    setInterval(() => location.reload(), 10000);
    </script>

    </body>
    </html>
    """