import os
import time
import threading
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

SYMBOL = "BTC"  # ✅ BTC olarak değiştirildi

POSITION_PERCENT = 0.98
TP_PERCENT = 0.002
SL_PERCENT = 0.001

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


# ✅ BTC price precision
def format_price(raw_price: float) -> float:
    return round(raw_price, 1)


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
        ex.order(
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

    except Exception as e:
        print("❌ TP ERROR:", e)

    # ===== SL =====
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))

    try:
        ex.order(
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

    except Exception as e:
        print("❌ SL ERROR:", e)


# ===== OPEN POSITION =====

def open_position(signal):
    print(f"{signal} açılıyor")

    ex = get_exchange()

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

        # ✅ BTC size precision
        size = round(usd_size / price, 4)

        # ✅ BTC minimum size
        if size < 0.001:
            size = 0.001

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
        except:
            fill_price = price

        # ===== POSITION WAIT =====
        actual_size = 0.0

        for i in range(10):
            time.sleep(1)
            actual_size = get_actual_position_size()

            if actual_size > 0.0005:
                break

        if actual_size < 0.0005:
            print("❌ Pozisyon açılmadı")
            return

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

    if is_position_open():
        print("⛔ Pozisyon açık → ignore")
        return

    open_position(signal)


# ===== WEBHOOK =====

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()

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
        print("WEBHOOK ERROR:", e)
        return {"status": "error", "message": str(e)}


# ===== ROOT =====

@app.get("/")
def root():
    return {"status": "alive"}
