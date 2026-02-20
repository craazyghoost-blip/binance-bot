import os
import time
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
LEVERAGE = 2
POSITION_PERCENT = 0.9
TIMEOUT_SECONDS = 300
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
last_signal_time = 0


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def open_position(signal):
    global current_position

    account_value = get_account_value()
    size = account_value * POSITION_PERCENT

    if size <= 0:
        print("Balance is zero.")
        return

    is_buy = True if signal == "BUY" else False

    print("Opening position:", signal, "Size:", size)

    result = exchange.market_open(SYMBOL, is_buy, size)

    print("ORDER RESULT:", result)

    current_position = signal

def close_position():
    global current_position
    print("Closing position")
    exchange.market_close(SYMBOL)
    current_position = None


@app.post("/webhook")
async def webhook(request: Request):
    global current_position, last_signal_time

    data = await request.json()
    signal = data.get("signal")

    print("SIGNAL RECEIVED:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    now = time.time()

    # Cooldown
    if now - last_signal_time < TIMEOUT_SECONDS:
        print("Cooldown active.")
        return {"status": "cooldown"}

    # Aynı sinyal tekrar gelirse işlem yapma
    if current_position == signal:
        print("Same position already open.")
        return {"status": "same_position"}

    # Ters sinyal gelirse kapat
    if current_position and current_position != signal:
        close_position()

    open_position(signal)

    last_signal_time = now

    return {"status": "ok"}
