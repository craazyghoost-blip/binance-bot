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
TP_PERCENT = 0.02
SL_PERCENT = 0.03

MIN_ORDER_USD = 15

# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)

print("BOT ADDRESS:", account.address)

exchange = None


def get_exchange():
    global exchange

    if exchange is None:
        exchange = Exchange(
            account,
            base_url="https://api.hyperliquid.xyz"
        )

    return exchange


def format_price(raw_price: float) -> float:
    return round(raw_price, 3)


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
        print("Position check error:", e)
        return False


# ===== CANCEL ORDERS =====

def cancel_all_orders():
    ex = get_exchange()

    try:
        open_orders = ex.info.open_orders(account.address)

        for o in open_orders:
            if o["coin"] == SYMBOL:
                ex.cancel(SYMBOL, o["oid"])

        print("🧹 Eski emirler temizlendi")

    except Exception as e:
        print("Cancel error:", e)


# ===== GET REAL POSITION SIZE =====

def get_actual_position_size():
    ex = get_exchange()

    try:
        state = ex.info.user_state(account.address)

        for p in state.get("assetPositions", []):
            if p["position"]["coin"] == SYMBOL:
                return abs(float(p["position"]["szi"]))

    except Exception as e:
        print("Position size error:", e)

    return 0.0


# ===== PLACE TP/SL =====

def place_tp_sl(ex, is_buy, actual_size, fill_price):
    close_side = not is_buy

    # ===== TP =====

    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))

    try:
        tp_result = ex.order(
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

        print(f"✅ TP konuldu @ {tp_price}")
        print("TP RESULT:", tp_result)

    except Exception as e:
        print("❌ TP ERROR:", e)

    # ===== SL =====

    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))

    try:
        sl_result = ex.order(
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

        print(f"✅ SL konuldu @ {sl_price}")
        print("SL RESULT:", sl_result)

    except Exception as e:
        print("❌ SL ERROR:", e)


# ===== OPEN POSITION =====

def open_position(signal):
    print(f"{signal} açılıyor")

    ex = get_exchange()

    # HER ZAMAN TEMİZ BAŞLA
    cancel_all_orders()

    try:
        state = ex.info.user_state(account.address)

        account_value = float(
            state["marginSummary"]["accountValue"]
        )

        price = float(
            ex.info.all_mids()[SYMBOL]
        )

        usd_size = max(
            account_value * POSITION_PERCENT,
            MIN_ORDER_USD
        )

        size = round(usd_size / price, 2)

        if size < 0.1:
            size = 0.1

        is_buy = signal == "BUY"

        print("Market order gönderiliyor...")

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

            print("Fill price:", fill_price)

        except Exception as e:
            print("⚠️ Fill price alınamadı:", e)

            # fallback
            fill_price = price

            print("Fallback price kullanılıyor:", fill_price)

        # ===== POSITION WAIT =====

        actual_size = 0.0

        for i in range(10):
            time.sleep(1)

            actual_size = get_actual_position_size()

            print(f"Pozisyon kontrol {i+1}/10 -> {actual_size}")

            if actual_size > 0.05:
                break

        if actual_size < 0.05:
            print("❌ Pozisyon açılmadı")
            return

        print("Gerçek pozisyon size:", actual_size)

        # ===== PLACE TP SL =====

        place_tp_sl(
            ex,
            is_buy,
            actual_size,
            fill_price
        )

        print("✅ Pozisyon hazır\n")

    except Exception as e:
        print("OPEN POSITION ERROR:", e)


# ===== SIGNAL PROCESS =====

def process_signal(signal):
    print(f"Sinyal: {signal}")

    # Pozisyon varsa ignore
    if is_position_open():
        print("⛔ Pozisyon açık → sinyal ignore")
        return

    open_position(signal)


# ===== WEBHOOK =====

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()

        signal = data.get("signal")

        if signal not in ["BUY", "SELL"]:
            return {
                "status": "ignored"
            }

        threading.Thread(
            target=process_signal,
            args=(signal,),
            daemon=True
        ).start()

        return {
            "status": "ok"
        }

    except Exception as e:
        print("WEBHOOK ERROR:", e)

        return {
            "status": "error",
            "message": str(e)
        }


# ===== ROOT =====

@app.get("/")
def root():
    return {
        "status": "alive"
    }
