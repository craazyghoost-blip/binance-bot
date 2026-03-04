import os
import time
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
POSITION_PERCENT = 0.97
TP_PERCENT = 0.019
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(
    wallet=account,
    account_address=account.address,
    base_url="https://api.hyperliquid.xyz"
)

current_position = None


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def open_position(signal):
    global current_position

    account_value = get_account_value()
    usd_size = account_value * POSITION_PERCENT

    mids = exchange.info.all_mids()
    btc_price = float(mids["BTC"])
    btc_size = round(usd_size / btc_price, 5)

    is_buy = signal == "BUY"

    print("Opening position:", signal)

    result = exchange.market_open(SYMBOL, is_buy, btc_size)
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
        tp_price = round(fill_price * (1 + TP_PERCENT), 2)
        tp_is_buy = False
    else:
        tp_price = round(fill_price * (1 - TP_PERCENT), 2)
        tp_is_buy = True

    tp_result = exchange.order(
        SYMBOL,
        tp_is_buy,
        size,
        tp_price,
        {"limit": {"tif": "Alo"}, "reduceOnly": True}
    )

    print("TP RESULT:", tp_result)

    current_position = signal


def close_position():
    global current_position

    if current_position is None:
        return

    exchange.market_close(SYMBOL)
    current_position = None


@app.post("/webhook")
async def webhook(request: Request):

    global current_position

    body = await request.json()
    signal = body.get("signal")

    print("SIGNAL RECEIVED:", signal)

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    if current_position and current_position != signal:
        close_position()
        time.sleep(1)

    open_position(signal)

    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}
