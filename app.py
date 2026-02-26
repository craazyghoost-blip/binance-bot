import os
import time
import threading
import requests
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
POSITION_PERCENT = 0.9
RANGE_TP = 0.002   # %0.20
# ==================

account = Account.from_key(PRIVATE_KEY)
exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None


# =============================
def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


# =============================
# RSI CALC
def get_rsi():

    data = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "3m", "limit": 100}
    ).json()

    closes = [float(c[4]) for c in data]

    gains, losses = [], []

    for i in range(1, 15):
        diff = closes[-i] - closes[-i-1]
        if diff >= 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))

    avg_gain = sum(gains)/14 if gains else 0.0001
    avg_loss = sum(losses)/14 if losses else 0.0001

    rs = avg_gain / avg_loss
    rsi = 100 - (100/(1+rs))

    print("RSI:", rsi)
    return rsi


# =============================
def close_position():
    global current_position

    if current_position is None:
        return

    print("Closing position")
    exchange.market_close(SYMBOL)
    current_position = None


# =============================
# RANGE TP MONITOR
def monitor_range_tp(entry_price, signal):

    global current_position

    while current_position == signal:

        mids = exchange.info.all_mids()
        price = float(mids["BTC"])

        if signal == "BUY":
            pnl = (price - entry_price) / entry_price
        else:
            pnl = (entry_price - price) / entry_price

        if pnl >= RANGE_TP:
            print("RANGE TP HIT")
            close_position()
            return

        time.sleep(2)


# =============================
def open_position(signal):

    global current_position

    account_value = get_account_value()
    usd_size = account_value * POSITION_PERCENT

    mids = exchange.info.all_mids()
    btc_price = float(mids["BTC"])
    btc_size = round(usd_size / btc_price, 5)

    is_buy = True if signal == "BUY" else False

    print("MARKET ENTRY:", signal)

    exchange.market_open(SYMBOL, is_buy, btc_size)

    current_position = signal

    # ===== RSI MODE =====
    rsi = get_rsi()

    # YATAY MARKET
    if 45 <= rsi <= 55:
        print("RANGE MODE ACTIVE")

        entry_price = btc_price

        threading.Thread(
            target=monitor_range_tp,
            args=(entry_price, signal),
            daemon=True
        ).start()


# =============================
@app.post("/webhook")
async def webhook(request: Request):

    global current_position

    body = await request.json()
    signal = body.get("signal", "").upper()

    print("SIGNAL:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    if current_position and current_position != signal:
        close_position()
        time.sleep(2)

    if current_position == signal:
        return {"status": "same_position"}

    open_position(signal)

    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}
