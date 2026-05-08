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

POSITION_PERCENT = 0.98

# TP / SL
TP_PERCENT = 0.002   # %0.2
SL_PERCENT = 0.001   # %0.1

MIN_ORDER_USD = 15

# SIGNAL SPAM KORUMA
SIGNAL_COOLDOWN = 15

# ===================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)

print("BOT ADDRESS:", account.address)

exchange = None

last_signal_time = 0
processing_signal = False


# ===== EXCHANGE =====

def get_exchange():

    global exchange

    if exchange is None:

        exchange = Exchange(
            account,
            base_url="https://api.hyperliquid.xyz"
        )

    return exchange


# ===== BTC PRICE FORMAT =====

def format_price(price: float):

    return round(price)


# ===== POSITION CHECK =====

def is_position_open():

    ex = get_exchange()

    try:

        state = ex.info.user_state(account.address)

        for p in state.get("assetPositions", []):

            if p["position"]["coin"] == SYMBOL:

                size = abs(float(p["position"]["szi"]))

                if size > 0:
                    return True

        return False

    except Exception as e:

        print("POSITION CHECK ERROR:", repr(e))
        return False


# ===== CANCEL ORDERS =====

def cancel_all_orders():

    ex = get_exchange()

    try:

        open_orders = ex.info.open_orders(account.address)

        for o in open_orders:

            if o["coin"] == SYMBOL:

                ex.cancel(SYMBOL, o["oid"])

                time.sleep(1)

        print("🧹 Eski emirler temizlendi")

    except Exception as e:

        print("CANCEL ERROR:", repr(e))


# ===== GET REAL POSITION SIZE =====

def get_actual_position_size():

    ex = get_exchange()

    try:

        state = ex.info.user_state(account.address)

        for p in state.get("assetPositions", []):

            if p["position"]["coin"] == SYMBOL:

                return abs(float(p["position"]["szi"]))

    except Exception as e:

        print("POSITION SIZE ERROR:", repr(e))

    return 0.0


# ===== PLACE TP / SL =====

def place_tp_sl(ex, is_buy, actual_size, fill_price):

    close_side = not is_buy

    # ===== FİYATLAR =====

    if is_buy:

        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        tp_price = format_price(fill_price * (1 + TP_PERCENT))

    else:

        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        tp_price = format_price(fill_price * (1 - TP_PERCENT))

    print("SL PRICE:", sl_price)
    print("TP PRICE:", tp_price)

    # =========================================================
    # 1. TUR
    # =========================================================

    # ===== SL FIRST =====

    time.sleep(3)

    try:

        sl_result_1 = ex.order(
            SYMBOL,
            close_side,
            actual_size,
            sl_price,
            order_type={
                "trigger": {
                    "triggerPx": sl_price,
                    "isMarket": True,
                    "tpsl": "sl"
                }
            },
            reduce_only=True
        )

        print("✅ SL 1 OK:", sl_result_1)

    except Exception as e:

        print("❌ SL 1 ERROR:", repr(e))

    # ===== TP SECOND =====

    time.sleep(3)

    try:

        tp_result_1 = ex.order(
            SYMBOL,
            close_side,
            actual_size,
            tp_price,
            order_type={
                "trigger": {
                    "triggerPx": tp_price,
                    "isMarket": True,
                    "tpsl": "tp"
                }
            },
            reduce_only=True
        )

        print("✅ TP 1 OK:", tp_result_1)

    except Exception as e:

        print("❌ TP 1 ERROR:", repr(e))

    # =========================================================
    # 2. TUR
    # =========================================================

    print("⏳ 2. TP/SL TURU BAŞLIYOR")

    time.sleep(5)

    # ===== 2. SL =====

    try:

        sl_result_2 = ex.order(
            SYMBOL,
            close_side,
            actual_size,
            sl_price,
            order_type={
                "trigger": {
                    "triggerPx": sl_price,
                    "isMarket": True,
                    "tpsl": "sl"
                }
            },
            reduce_only=True
        )

        print("✅ SL 2 OK:", sl_result_2)

    except Exception as e:

        print("❌ SL 2 ERROR:", repr(e))

    # ===== 2. TP =====

    time.sleep(3)

    try:

        tp_result_2 = ex.order(
            SYMBOL,
            close_side,
            actual_size,
            tp_price,
            order_type={
                "trigger": {
                    "triggerPx": tp_price,
                    "isMarket": True,
                    "tpsl": "tp"
                }
            },
            reduce_only=True
        )

        print("✅ TP 2 OK:", tp_result_2)

    except Exception as e:

        print("❌ TP 2 ERROR:", repr(e))


# ===== OPEN POSITION =====

def open_position(signal):

    print(f"{signal} açılıyor")

    ex = get_exchange()

    cancel_all_orders()

    try:

        time.sleep(2)

        state = ex.info.user_state(account.address)

        account_value = float(
            state["marginSummary"]["accountValue"]
        )

        time.sleep(2)

        price = float(
            ex.info.all_mids()[SYMBOL]
        )

        usd_size = max(
            account_value * POSITION_PERCENT,
            MIN_ORDER_USD
        )

        # BTC SIZE
        size = round(usd_size / price, 5)

        is_buy = signal == "BUY"

        print("MARKET ORDER GÖNDERİLİYOR")
        print("SIZE:", size)
        print("PRICE:", price)

        time.sleep(2)

        result = ex.market_open(
            SYMBOL,
            is_buy,
            size
        )

        print("MARKET RESULT:", result)

        # ===== FILL PRICE =====

        try:

            fill_price = float(
                result["response"]["data"]["statuses"][0]["filled"]["avgPx"]
            )

        except:

            fill_price = price

        print("FILL PRICE:", fill_price)

        # ===== WAIT POSITION =====

        actual_size = 0.0

        for i in range(15):

            time.sleep(2)

            actual_size = get_actual_position_size()

            print(f"POSITION CHECK {i+1}: {actual_size}")

            if actual_size > 0:
                break

        if actual_size <= 0:

            print("❌ POZİSYON AÇILMADI")
            return

        print("GERÇEK SIZE:", actual_size)

        # EXCHANGE STATE OTURSUN
        time.sleep(5)

        # ===== TP / SL =====

        place_tp_sl(
            ex,
            is_buy,
            actual_size,
            fill_price
        )

        print("✅ POZİSYON HAZIR\n")

    except Exception as e:

        print("OPEN POSITION ERROR:", repr(e))


# ===== PROCESS SIGNAL =====

def process_signal(signal):

    global last_signal_time
    global processing_signal

    # AYNI ANDA 2 THREAD ENGELİ
    if processing_signal:

        print("⛔ ZATEN PROCESS ÇALIŞIYOR")
        return

    processing_signal = True

    try:

        now = time.time()

        # SIGNAL SPAM KORUMA
        if now - last_signal_time < SIGNAL_COOLDOWN:

            print("⛔ SIGNAL COOLDOWN AKTİF")
            return

        last_signal_time = now

        print("SIGNAL:", signal)

        if is_position_open():

            print("⛔ ZATEN POZİSYON VAR")
            return

        open_position(signal)

    finally:

        processing_signal = False


# ===== WEBHOOK =====

@app.post("/webhook")
async def webhook(request: Request):

    try:

        data = await request.json()

        print("WEBHOOK DATA:", data)

        signal = data.get("signal")

        if signal not in ["BUY", "SELL"]:

            return {"status": "ignored"}

        threading.Thread(
            target=process_signal,
            args=(signal,),
            daemon=True
        ).start()

        return {"status": "ok"}

    except Exception as e:

        print("WEBHOOK ERROR:", repr(e))

        return {
            "status": "error",
            "message": str(e)
        }


# ===== ROOT =====

@app.get("/")
def root():

    return {"status": "alive"}
