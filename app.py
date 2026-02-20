from fastapi import FastAPI
from hyperliquid.exchange import Exchange
from eth_account import Account
import os
import time
import threading

app = FastAPI()

# ========= CONFIG =========
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
LEVERAGE = 2
POSITION_PERCENT = 0.9
TIMEOUT_SECONDS = 300  # 5 dakika
# ==========================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)
exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
last_signal_time = 0


# ========= HELPER FUNCTIONS =========

def get_account_value():
    state = exchange.info.user_state(account.address)
    print("USER STATE:", state)
    return float(state["marginSummary"]["accountValue"])


def open_position(signal):
    global current_position

    is_buy = True if signal == "BUY" else False

    account_value = get_account_value()
    size = account_value * POSITION_PERCENT

    print("Opening position:", signal, "Size:", size)

    exchange.market_open(
        SYMBOL,
        is_buy,
        size
    )

    current_position = signal


def close_position():
    global current_position

    if current_position is None:
        return

    print("Closing position")

    exchange.market_close(SYMBOL)

    current_position = None


# ========= TIMEOUT WATCHER =========

def timeout_watcher():
    global last_signal_time, current_position

    while True:
        time.sleep(5)

        if current_position is None:
            continue

        if time.time() - last_signal_time > TIMEOUT_SECONDS:
            print("5 MIN TIMEOUT - Closing position")
            close_position()


threading.Thread(target=timeout_watcher, daemon=True).start()


# ========= WEBHOOK =========

@app.post("/webhook")
async def webhook(data: dict):
    global current_position, last_signal_time

    signal = data.get("signal")

    print("SIGNAL RECEIVED:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    last_signal_time = time.time()

    # Aynı sinyalse işlem açma
    if current_position == signal:
        print("Same signal ignored")
        return {"status": "same signal ignored"}

    # Ters sinyal geldiyse önce kapat
    if current_position is not None:
        close_position()

    try:
        open_position(signal)
        return {"status": "order sent"}

    except Exception as e:
        print("ORDER ERROR:", str(e))
        return {"error": str(e)}


@app.get("/")
def root():
    return {"status": "bot running"}
