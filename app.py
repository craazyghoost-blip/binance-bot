import os
import time
import threading
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "SOL"
POSITION_PERCENT = 0.97
TP_PERCENT = 0.0045
SL_PERCENT = 0.01
MIN_ORDER_USD = 10
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
current_tp_id = None
current_sl_id = None


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
        time.sleep(1)
    except Exception as e:
        print("TP cancel error:", e)

    current_tp_id = None


def cancel_sl():
    global current_sl_id

    if current_sl_id is None:
        return

    try:
        exchange.cancel(SYMBOL, current_sl_id)
        print("SL CANCELLED:", current_sl_id)
        time.sleep(1)
    except Exception as e:
        print("SL cancel error:", e)

    current_sl_id = None


def open_position(signal):
    global current_position, current_tp_id, current_sl_id

    account_value = get_account_value()
    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)

    mids = exchange.info.all_mids()
    sol_price = float(mids[SYMBOL])

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

    time.sleep(1)

    state = exchange.info.user_state(account.address)

    size = 0
    for p in state["assetPositions"]:
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))

    if size == 0:
        print("Position not found")
        return

    # ===== TP =====
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_is_buy = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_is_buy = True

    tp_result = exchange.order(
        SYMBOL,
        tp_is_buy,
        size,
        tp_price,
        {"limit": {"tif": "Gtc"}}
    )

    print("TP SET:", tp_price)

    try:
        current_tp_id = tp_result["response"]["data"]["statuses"][0]["resting"]["oid"]
    except:
        print("TP oid alınamadı")

    # ===== SL =====
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_is_buy = False
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_is_buy = True

    sl_result = exchange.order(
        SYMBOL,
        sl_is_buy,
        size,
        sl_price,
        {"limit": {"tif": "Gtc"}}
    )

    print("SL SET:", sl_price)

    try:
        current_sl_id = sl_result["response"]["data"]["statuses"][0]["resting"]["oid"]
    except:
        print("SL oid alınamadı")

    current_position = signal


def close_position():
    global current_position

    if current_position is None:
        return

    exchange.market_close(SYMBOL)

    cancel_tp()
    cancel_sl()

    current_position = None


def process_signal(signal):
    global current_position

    cancel_tp()
    cancel_sl()
    time.sleep(1)

    if current_position and current_position != signal:
        close_position()
        time.sleep(1)

    open_position(signal)


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
