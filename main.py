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

USERNAME = os.getenv("IOL_USER")
PASSWORD = os.getenv("IOL_PASS")

# ==========================
# STATE
# ==========================
token = None
token_expiry = 0

history = {s: deque(maxlen=HISTORY_LEN) for s in SYMBOLS}
last_signals = {}

bot_running = False  # 🔥 evita múltiples threads

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

            print("[LOGIN FAIL]", r.text, flush=True)

        except Exception as e:
            print("[LOGIN ERROR]", e, flush=True)

        time.sleep(2)

    print("[LOGIN GAVE UP]", flush=True)


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
            print(f"[HTTP ERROR] {symbol} {r.status_code}", flush=True)
            return None

        data = r.json()

        price = data.get("ultimoPrecio")
        puntas = data.get("puntas", [])

        print(f"[DATA] {symbol} {price}", flush=True)

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
        print(f"[ERROR] {symbol} {e}", flush=True)
        return None

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
                    last_signals[s] = {
                        "price": 0,
                        "signal": "ERROR",
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    continue

                history[s].append(d)

                signal = compute_signal(history[s])

                last_signals[s] = {
                    "price": d["price"],
                    "signal": signal,
                    "time": datetime.now().strftime("%H:%M:%S")
                }

                print(f"[SIGNAL] {s} {d['price']} {signal}", flush=True)

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

    print("[STARTUP EVENT]", flush=True)

    if not bot_running:
        print("[STARTING BOT THREAD]", flush=True)

        t = threading.Thread(target=bot_loop, daemon=True)
        t.start()

        bot_running = True
    else:
        print("[BOT ALREADY RUNNING]", flush=True)

@app.get("/", response_class=HTMLResponse)
def home():
    html = "<h1>📊 BOT EN VIVO</h1>"

    if not last_signals:
        html += "<p>Cargando datos...</p>"

    for s, d in last_signals.items():
        html += f"""
        <div>
            <h2>{s}</h2>
            <p>Precio: {d['price']}</p>
            <p>Señal: <b>{d['signal']}</b></p>
            <p>Hora: {d['time']}</p>
        </div>
        <hr>
        """

    return html

@app.get("/data")
def data():
    return JSONResponse(content=last_signals)