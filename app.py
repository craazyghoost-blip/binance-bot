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
TP_PERCENT = 0.004
SL_PERCENT = 0.006
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


def format_price(price):
    return float(f"{price:.3f}")


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def cancel_tp():
    global current_tp_id
    if current_tp_id:
        try:
            exchange.cancel(SYMBOL, current_tp_id)
        except:
            pass
        current_tp_id = None


def cancel_sl():
    global current_sl_id
    if current_sl_id:
        try:
            exchange.cancel(SYMBOL, current_sl_id)
        except:
            pass
        current_sl_id = None


def open_position(signal):
    global current_position, current_tp_id, current_sl_id

    print("Signal geldi, 3 sn bekleniyor...")
    time.sleep(3)

    account_value = get_account_value()
    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)

    price = float(exchange.info.all_mids()[SYMBOL])
    size = round(usd_size / price, 3)

    is_buy = signal == "BUY"

    print("Pozisyon açılıyor:", signal)

    result = exchange.market_open(SYMBOL, is_buy, size)

    try:
        fill_price = float(
            result["response"]["data"]["statuses"][0]["filled"]["avgPx"]
        )
    except:
        print("Fill price yok")
        return

    print("Pozisyon açıldı, 3 sn bekleniyor...")
    time.sleep(3)

    # pozisyon size çek
    state = exchange.info.user_state(account.address)
    size = 0
    for p in state["assetPositions"]:
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))

    if size == 0:
        print("Pozisyon yok")
        return

    # ===== TP =====
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_side = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_side = True

    print("TP için 3 sn bekleniyor...")
    time.sleep(3)

    tp = exchange.order(
        SYMBOL,
        tp_side,
        size,
        tp_price,
        {"limit": {"tif": "Gtc"}, "reduceOnly": True}
    )

    try:
        current_tp_id = tp["response"]["data"]["statuses"][0]["resting"]["oid"]
        print("TP koyuldu:", tp_price)
    except:
        print("TP hata")

    # ===== SL =====
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_side = False
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_side = True

    print("SL için 3 sn bekleniyor...")
    time.sleep(3)

    sl = exchange.order(
        SYMBOL,
        sl_side,
        size,
        sl_price,
        {"limit": {"tif": "Gtc"}, "reduceOnly": True}
    )

    try:
        current_sl_id = sl["response"]["data"]["statuses"][0]["resting"]["oid"]
        print("SL koyuldu:", sl_price)
    except:
        print("SL hata")

    current_position = signal


def close_position():
    global current_position

    if current_position:
        exchange.market_close(SYMBOL)
        cancel_tp()
        cancel_sl()
        current_position = None


def process_signal(signal):
    global current_position

    cancel_tp()
    cancel_sl()

    if current_position and current_position != signal:
        close_position()
        time.sleep(1)

    open_position(signal)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    signal = data.get("signal")

    print("SIGNAL:", signal)

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
