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
TP_PERCENT = 0.005
SL_PERCENT = 0.006
MIN_ORDER_USD = 10
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")
print("✅ Exchange başarıyla başlatıldı.")

current_position = None
current_tp_id = None


def format_price(price: float) -> float:
    return round(price, 2)


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


def get_position_size():
    for i in range(12):
        state = exchange.info.user_state(account.address)

        for p in state["assetPositions"]:
            if p["position"]["coin"] == SYMBOL:
                size = abs(float(p["position"]["szi"]))
                if size > 0:
                    print(f"Position bulundu: {size}")
                    return size

        print(f"Pozisyon bekleniyor... ({i+1})")
        time.sleep(1)

    return 0


def open_position(signal):
    global current_position, current_tp_id

    account_value = get_account_value()
    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)

    mids = exchange.info.all_mids()
    price = float(mids[SYMBOL])

    size = round(usd_size / price, 2)

    print(f"ACCOUNT: {account_value} | PRICE: {price} | SIZE: {size} | {signal}")

    is_buy = signal == "BUY"

    # MARKET ORDER
    print("🚀 Market order gönderiliyor...")
    result = exchange.market_open(SYMBOL, is_buy, size)
    print("MARKET OPEN RESULT:", result)

    try:
        fill_price = float(
            result["response"]["data"]["statuses"][0]["filled"]["avgPx"]
        )
    except:
        print("❌ Fill price alınamadı")
        return

    # POSITION WAIT
    size = get_position_size()
    if size == 0:
        print("❌ Position yok, TP/SL atlanıyor")
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

    print("🎯 TP SET:", tp_price)

    try:
        current_tp_id = tp_result["response"]["data"]["statuses"][0]["resting"]["oid"]
    except:
        current_tp_id = None

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
        {"trigger": {"triggerPx": sl_price, "isMarket": True}}
    )

    print("🛑 SL SET:", sl_price)

    current_position = signal
    print(f"✅ {signal} pozisyonu tamamlandı.")


def close_position():
    global current_position

    if current_position is None:
        return

    print("Pozisyon kapatılıyor...")
    exchange.market_close(SYMBOL)
    current_position = None


def process_signal(signal):
    global current_position

    # aynı sinyal → işlem açma
    if current_position == signal:
        print("Aynı sinyal, işlem açılmadı")
        return

    cancel_tp()
    time.sleep(1)

    if current_position:
        close_position()
        time.sleep(2)

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
