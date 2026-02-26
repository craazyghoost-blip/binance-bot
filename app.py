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

OFFSET = 0.0002
TP_PERCENT = 0.001
ORDER_TIMEOUT = 60
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
pending_order = False


# =============================
def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


# =============================
def set_take_profit(signal):

    state = exchange.info.user_state(account.address)
    positions = state["assetPositions"]

    if not positions:
        return

    pos = positions[0]["position"]

    entry_price = float(pos["entryPx"])
    size = abs(float(pos["szi"]))

    if signal == "BUY":
        tp_price = entry_price * (1 + TP_PERCENT)
        is_buy = False
    else:
        tp_price = entry_price * (1 - TP_PERCENT)
        is_buy = True

    exchange.order(
        SYMBOL,
        is_buy,
        size,
        tp_price,
        {"limit": {"tif": "Gtc"}}
    )

    print("TP SET:", tp_price)


# =============================
def monitor_fill(signal):

    global current_position, pending_order

    start_time = time.time()

    while time.time() - start_time < ORDER_TIMEOUT:

        state = exchange.info.user_state(account.address)
        positions = state["assetPositions"]

        if len(positions) > 0:
            print("POSITION FILLED")
            current_position = signal
            pending_order = False
            set_take_profit(signal)
            return

        time.sleep(2)

    print("ORDER TIMEOUT → CANCEL")
    exchange.cancel_all(SYMBOL)
    pending_order = False


# =============================
def open_position(signal):

    global pending_order

    if pending_order:
        print("Order already pending")
        return

    account_value = get_account_value()
    usd_size = account_value * POSITION_PERCENT

    mids = exchange.info.all_mids()
    btc_price = float(mids["BTC"])
    btc_size = round(usd_size / btc_price, 5)

    if signal == "BUY":
        is_buy = True
        limit_price = btc_price * (1 - OFFSET)
    else:
        is_buy = False
        limit_price = btc_price * (1 + OFFSET)

    print("LIMIT ORDER:", signal, limit_price)

    exchange.order(
        SYMBOL,
        is_buy,
        btc_size,
        limit_price,
        {"limit": {"tif": "Gtc"}}
    )

    pending_order = True

    threading.Thread(
        target=monitor_fill,
        args=(signal,),
        daemon=True
    ).start()


# =============================
def close_position():
    global current_position

    if current_position is None:
        return

    print("Closing position")
    exchange.market_close(SYMBOL)
    current_position = None


# =============================
@app.post("/webhook")
async def webhook(request: Request):

    global current_position, pending_order

    body = await request.json()
    signal = body.get("signal")

    print("SIGNAL:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    # ✅ BEKLEYEN LIMIT VARSA İPTAL
    if pending_order:
        print("Cancel pending limit")
        exchange.cancel_all(SYMBOL)
        pending_order = False

    # ✅ TERS POZİSYON KAPAT
    if current_position and current_position != signal:
        close_position()
        time.sleep(1)

    if current_position == signal:
        return {"status": "same_position"}

    open_position(signal)

    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}
