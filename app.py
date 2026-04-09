import os
import time
import threading
import requests
import pandas as pd
import ta

from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "SOL"
POSITION_PERCENT = 0.97
TP_PERCENT = 0.003
MIN_ORDER_USD = 10
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
current_tp_id = None

# ===== INDICATORS =====
def get_indicators(symbol="SOL"):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1m&limit=150"
    data = requests.get(url).json()

    df = pd.DataFrame(data)
    df[1] = df[1].astype(float)
    df[2] = df[2].astype(float)
    df[3] = df[3].astype(float)
    df[4] = df[4].astype(float)
    df[5] = df[5].astype(float)

    close = df[4]
    high = df[2]
    low = df[3]
    volume = df[5]

    return {
        "price": close.iloc[-1],
        "rsi": ta.momentum.RSIIndicator(close).rsi().iloc[-1],
        "macd": ta.trend.MACD(close).macd_diff().iloc[-1],
        "adx": ta.trend.ADXIndicator(high, low, close).adx().iloc[-1],
        "ema50": ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1],
        "ema200": ta.trend.EMAIndicator(close, window=200).ema_indicator().iloc[-1],
        "atr": ta.volatility.AverageTrueRange(high, low, close).average_true_range().iloc[-1],
        "volume": volume.iloc[-1],
        "vol_avg": volume.rolling(20).mean().iloc[-1]
    }

# ===== FILTER =====
def filter_signal(signal, d):
    price = d["price"]
    rsi = d["rsi"]
    macd = d["macd"]
    adx = d["adx"]
    ema50 = d["ema50"]
    ema200 = d["ema200"]
    atr = d["atr"]
    volume = d["volume"]
    vol_avg = d["vol_avg"]

    # GLOBAL
    if adx < 23:
        return False

    if atr < price * 0.0025:
        return False

    if volume < vol_avg:
        return False

    # LONG
    if signal == "BUY":
        if not (price > ema50 > ema200):
            return False
        if not (45 < rsi < 60):
            return False
        if macd < 0:
            return False
        return True

    # SHORT
    if signal == "SELL":
        if not (price < ema50 < ema200):
            return False
        if not (40 < rsi < 55):
            return False
        if macd > 0:
            return False
        return True

    return False

# ===== CORE =====
def format_price(raw_price: float) -> float:
    return round(raw_price, 2)


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def cancel_tp():
    global current_tp_id

    if current_tp_id is None:
        return

    try:
        exchange.cancel(SYMBOL, current_tp_id)
        print("TP CANCELLED:", current_tp_id)
        time.sleep(2)
    except Exception as e:
        print("TP cancel error:", e)

    current_tp_id = None


def open_position(signal):
    global current_position
    global current_tp_id

    account_value = get_account_value()
    usd_size = account_value * POSITION_PERCENT

    mids = exchange.info.all_mids()
    sol_price = float(mids[SYMBOL])

    usd_size = max(usd_size, MIN_ORDER_USD)

    raw_size = usd_size / sol_price
    min_size = MIN_ORDER_USD / sol_price

    sol_size = max(round(raw_size, 2), round(min_size, 2))

    print("SOL PRICE:", sol_price)
    print("USD SIZE:", usd_size)
    print("SOL SIZE:", sol_size)

    is_buy = signal == "BUY"

    print("Opening position:", signal)

    result = exchange.market_open(SYMBOL, is_buy, sol_size)
    print("ORDER RESULT:", result)

    try:
        fill_price = float(
            result["response"]["data"]["statuses"][0]["filled"]["avgPx"]
        )
    except:
        print("Fill price alınamadı")
        return

    time.sleep(2)

    state = exchange.info.user_state(account.address)

    size = 0
    for p in state["assetPositions"]:
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))

    if size == 0:
        print("Position not found")
        return

    if is_buy:
        raw_tp = fill_price * (1 + TP_PERCENT)
        tp_is_buy = False
    else:
        raw_tp = fill_price * (1 - TP_PERCENT)
        tp_is_buy = True

    tp_price = format_price(raw_tp)

    tp_result = exchange.order(
        SYMBOL,
        tp_is_buy,
        size,
        tp_price,
        {"limit": {"tif": "Gtc"}}
    )

    print("TP SET:", tp_price)
    print("TP RESULT:", tp_result)

    try:
        current_tp_id = tp_result["response"]["data"]["statuses"][0]["resting"]["oid"]
        print("TP OID:", current_tp_id)
    except:
        print("TP oid alınamadı")

    current_position = signal


def close_position():
    global current_position

    if current_position is None:
        return

    exchange.market_close(SYMBOL)
    current_position = None


# ===== SIGNAL PROCESS =====
def process_signal(signal):
    global current_position

    print("SIGNAL:", signal)
    print("Checking filters...")

    # aynı yön → tekrar açma
    if current_position == signal:
        print("Same direction → skip")
        return

    data = get_indicators(SYMBOL)

    print("INDICATORS:", data)

    if not filter_signal(signal, data):
        print("❌ FILTER REJECTED")
        return

    print("✅ FILTER APPROVED")

    cancel_tp()
    time.sleep(2)

    if current_position and current_position != signal:
        close_position()
        time.sleep(2)

    open_position(signal)


# ===== WEBHOOK =====
@app.post("/webhook")
async def webhook(request: Request):

    body = await request.json()
    signal = body.get("signal")

    print("SIGNAL RECEIVED:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    threading.Thread(
        target=process_signal,
        args=(signal,),
        daemon=True
    ).start()

    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}
