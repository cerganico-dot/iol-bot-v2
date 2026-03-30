import os
import time
import json
import math
import threading
import requests
from collections import deque
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ==========================
# CONFIG
# ==========================
BASE_URL = "https://api.invertironline.com"
LOGIN_URL = f"{BASE_URL}/token"
QUOTE_URL = f"{BASE_URL}/api/v2/bCBA/Titulos"

SYMBOLS = ["AL30", "GD30"]

REFRESH = int(os.getenv("REFRESH", 5))
HISTORY_LEN = int(os.getenv("HISTORY_LEN", 20))

USERNAME = os.getenv("IOL_USER")
PASSWORD = os.getenv("IOL_PASS")

PORT = int(os.getenv("PORT", 8080))

# ==========================
# STATE
# ==========================
token = None
token_expiry = 0
history = {s: deque(maxlen=HISTORY_LEN) for s in SYMBOLS}
last_signals = {}

# ==========================
# AUTH
# ==========================
def login():
    global token, token_expiry
    r = requests.post(LOGIN_URL, data={
        "username": USERNAME,
        "password": PASSWORD,
        "grant_type": "password"
    })
    if r.status_code == 200:
        data = r.json()
        token = data["access_token"]
        token_expiry = time.time() + data["expires_in"] - 60
        print("[LOGIN OK]")
    else:
        print("[LOGIN ERROR]", r.text)

def headers():
    global token
    if token is None or time.time() > token_expiry:
        login()
    return {"Authorization": f"Bearer {token}"}

# ==========================
# DATA
# ==========================
def get_quote(symbol):
    try:
        r = requests.get(f"{QUOTE_URL}/{symbol}/Cotizacion", headers=headers())
        data = r.json()

        price = data.get("ultimoPrecio")
        puntas = data.get("puntas", [])

        if not price:
            return None

        if not puntas:
            return {"price": price, "bid": 0, "ask": 0}

        p = puntas[0]
        return {
            "price": price,
            "bid": p["cantidadCompra"],
            "ask": p["cantidadVenta"]
        }

    except:
        return None

# ==========================
# LOGICA
# ==========================
def compute_signal(hist):
    if len(hist) < 3:
        return "WAIT"

    last = hist[-1]
    prev = hist[-2]

    # sin liquidez
    if last["bid"] == 0 and last["ask"] == 0:
        return "SIN MERCADO"

    # imbalance
    if last["ask"] > 0 and last["bid"] / last["ask"] > 10:
        return "BUY"

    # momentum
    if last["price"] > prev["price"]:
        return "UP"
    elif last["price"] < prev["price"]:
        return "DOWN"

    return "FLAT"

# ==========================
# BOT LOOP
# ==========================
def bot_loop():
    print("[BOT STARTED]")
    while True:
        try:
            for s in SYMBOLS:
                d = get_quote(s)
                if not d:
                    continue

                history[s].append(d)
                signal = compute_signal(history[s])

                last_signals[s] = {
                    "price": d["price"],
                    "signal": signal,
                    "time": datetime.now().strftime("%H:%M:%S")
                }

                print(s, d["price"], signal)

            time.sleep(REFRESH)

        except Exception as e:
            print("[ERROR]", e)
            time.sleep(5)

# ==========================
# DASHBOARD
# ==========================
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def home():
    html = "<h1>📊 BOT EN VIVO</h1>"

    for s, data in last_signals.items():
        html += f"""
        <div>
            <h2>{s}</h2>
            <p>Precio: {data['price']}</p>
            <p>Señal: <b>{data['signal']}</b></p>
            <p>Hora: {data['time']}</p>
        </div>
        <hr>
        """

    return html

# ==========================
# START
# ==========================
if __name__ == "__main__":
    t = threading.Thread(target=bot_loop)
    t.start()

    uvicorn.run(app, host="0.0.0.0", port=PORT)