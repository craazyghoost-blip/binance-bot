import os
import time
import threading
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
POSITION_PERCENT = 0.9
TIMEOUT_SECONDS = 300
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
position_open_time = None


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def open_position(signal):
    global current_position, position_open_time

    account_value = get_account_value()
    usd_size = account_value * POSITION_PERCENT

    if usd_size <= 0:
        print("Balance is zero.")
        return

    mids = exchange.info.all_mids()
    btc_price = float(mids["BTC"])
    btc_size = round(usd_size / btc_price, 5)

    is_buy = True if signal == "BUY" else False

    print("Opening position:", signal)
    print("BTC size:", btc_size)

    result = exchange.market_open(SYMBOL, is_buy, btc_size)
    print("ORDER RESULT:", result)

    current_position = signal
    position_open_time = time.time()


def close_position():
    global current_position, position_open_time

    if current_position is None:
        return

    print("Closing position after 300 seconds")
    exchange.market_close(SYMBOL)

    current_position = None
    position_open_time = None


# 🔥 Arka planda sürekli çalışan zaman kontrolü
def timeout_watcher():
    global current_position, position_open_time

    while True:
        if current_position and position_open_time:
            elapsed = time.time() - position_open_time
            if elapsed >= TIMEOUT_SECONDS:
                close_position()
        time.sleep(1)


# Thread başlat
threading.Thread(target=timeout_watcher, daemon=True).start()


@app.post("/webhook")
async def webhook(request: Request):
    global current_position

    body = await request.json()
    signal = body.get("signal")

    print("SIGNAL RECEIVED:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    # Aynı pozisyon varsa işlem yapma
    if current_position == signal:
        return {"status": "same_position"}

    # Ters sinyal geldiyse kapat
    if current_position and current_position != signal:
        close_position()
        time.sleep(1)

    open_position(signal)

    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}
