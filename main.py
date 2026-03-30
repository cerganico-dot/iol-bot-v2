import os
import time
import json
import math
import requests
from collections import deque
from datetime import datetime

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

TIMEOUT = 5
MODEL_FILE = "model.json"

# ==========================
# GLOBAL STATE
# ==========================
token = None
token_expiry = 0

history = {
    symbol: deque(maxlen=HISTORY_LEN) for symbol in SYMBOLS
}

model = {
    "weights": [0.0] * 5,
    "bias": 0.0,
    "lr": 0.01
}

# ==========================
# AUTH
# ==========================
def login():
    global token, token_expiry
    try:
        response = requests.post(
            LOGIN_URL,
            data={
                "username": USERNAME,
                "password": PASSWORD,
                "grant_type": "password"
            },
            timeout=TIMEOUT
        )

        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            token_expiry = time.time() + expires_in - 60
            print("[LOGIN OK]", flush=True)
        else:
            print(f"[LOGIN ERROR] {response.status_code}", flush=True)

    except Exception as e:
        print(f"[LOGIN EXCEPTION] {e}", flush=True)


def get_headers():
    global token
    if token is None or time.time() > token_expiry:
        login()
    return {"Authorization": f"Bearer {token}"}

# ==========================
# DATA FETCH (DEBUG ROBUSTO)
# ==========================
def get_quote(symbol):
    try:
        url = f"{QUOTE_URL}/{symbol}/Cotizacion"
        response = requests.get(url, headers=get_headers(), timeout=TIMEOUT)

        if response.status_code != 200:
            print(f"[HTTP ERROR] {symbol} {response.status_code}", flush=True)
            return None

        data = response.json()

        if not isinstance(data, dict):
            print(f"[FORMAT ERROR] {symbol}", flush=True)
            return None

        price = data.get("ultimoPrecio")
        puntas = data.get("puntas", [])

        if not isinstance(puntas, list) or len(puntas) == 0:
            print(f"[NO BOOK] {symbol}", flush=True)
            return None

        punta = puntas[0] if isinstance(puntas[0], dict) else {}

        bid_size = punta.get("cantidadCompra")
        ask_size = punta.get("cantidadVenta")

        if price is None:
            print(f"[NO PRICE] {symbol}", flush=True)
            return None

        if bid_size is None or ask_size is None:
            print(f"[NO SIZE] {symbol}", flush=True)
            return None

        return {
            "price": float(price),
            "bid_size": float(bid_size),
            "ask_size": float(ask_size)
        }

    except Exception as e:
        print(f"[DATA ERROR] {symbol} {e}", flush=True)
        return None

# ==========================
# SIGNALS
# ==========================
def detect_persistence(hist):
    if len(hist) < 5:
        return False
    large = [x for x in hist if x["bid_size"] > 50000 or x["ask_size"] > 50000]
    return len(large) >= int(len(hist) * 0.6)


def detect_spoofing(hist):
    if len(hist) < 5:
        return False
    spikes = [x for x in hist if x["bid_size"] > 70000 or x["ask_size"] > 70000]
    if len(spikes) < 2:
        return False
    last = hist[-1]
    return last["bid_size"] < 20000 and last["ask_size"] < 20000


def detect_absorption(hist):
    if len(hist) < 5:
        return False
    prices = [x["price"] for x in hist]
    return max(prices) - min(prices) < 0.2


def detect_momentum(hist):
    if len(hist) < 2:
        return 0
    start = hist[0]["price"]
    end = hist[-1]["price"]
    if end > start:
        return 1
    elif end < start:
        return -1
    return 0

# ==========================
# SCORING
# ==========================
def compute_score(hist):
    score = 50

    if detect_persistence(hist):
        score += 40
    if detect_absorption(hist):
        score += 30
    if detect_spoofing(hist):
        score -= 50

    momentum = detect_momentum(hist)
    if momentum > 0:
        score += 20
    elif momentum < 0:
        score -= 20

    return max(0, min(100, score))


def decision(score):
    if score > 70:
        return "COMPRAR"
    elif score >= 50:
        return "HOLD"
    return "NO TRADE"

# ==========================
# ML MODEL
# ==========================
def load_model():
    global model
    if os.path.exists(MODEL_FILE):
        try:
            with open(MODEL_FILE, "r") as f:
                model = json.load(f)
                print("[MODEL LOADED]", flush=True)
        except:
            print("[MODEL LOAD ERROR]", flush=True)


def save_model():
    try:
        with open(MODEL_FILE, "w") as f:
            json.dump(model, f)
    except:
        pass


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def extract_features(hist):
    if len(hist) < 3:
        return None

    last = hist[-1]
    prev = hist[-2]

    price = last["price"]
    bid = last["bid_size"]
    ask = last["ask_size"]

    imbalance = (bid - ask) / (bid + ask + 1)
    spread = abs(bid - ask)
    momentum = price - prev["price"]

    return [bid, ask, imbalance, spread, momentum]


def predict(features):
    z = model["bias"]
    for i in range(len(features)):
        z += model["weights"][i] * features[i]
    return sigmoid(z)


def train(features, label):
    pred = predict(features)
    error = label - pred

    for i in range(len(model["weights"])):
        model["weights"][i] += model["lr"] * error * features[i]

    model["bias"] += model["lr"] * error


def generate_label(hist, horizon=3):
    if len(hist) < horizon + 1:
        return None
    current = hist[-horizon]["price"]
    future = hist[-1]["price"]
    return 1 if future > current else 0

# ==========================
# MAIN LOOP
# ==========================
def run():
    print("[BOT STARTED]", flush=True)
    load_model()

    while True:
        try:
            for symbol in SYMBOLS:
                data = get_quote(symbol)

                if data is None:
                    continue

                history[symbol].append(data)

                # ML
                features = extract_features(history[symbol])
                ml_prob = None

                if features:
                    ml_prob = predict(features)

                label = generate_label(history[symbol])

                if features and label is not None:
                    train(features, label)

                # Score híbrido
                score = compute_score(history[symbol])

                if ml_prob is not None:
                    score = int(0.7 * score + 0.3 * (ml_prob * 100))

                action = decision(score)

                timestamp = datetime.now().strftime("%H:%M:%S")

                print(
                    f"{timestamp} | {symbol} | {data['price']:.2f} | {action} | Prob: {score}%",
                    flush=True
                )

            # guardar modelo cada 60s
            if int(time.time()) % 60 == 0:
                save_model()

            time.sleep(REFRESH)

        except Exception as e:
            print(f"[LOOP ERROR] {e}", flush=True)
            time.sleep(5)

# ==========================
# ENTRYPOINT
# ==========================
if __name__ == "__main__":
    run()