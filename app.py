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
SL_PERCENT = 0.006      # SL ekledik
MIN_ORDER_USD = 15
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
    return round(raw_price, 3)   # daha hassas


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
    sol_size = round(raw_size, 2)
    if sol_size < 0.1:
        sol_size = 0.1

    print("SOL PRICE:", sol_price)
    print("USD SIZE:", usd_size)
    print("SOL SIZE:", sol_size)

    is_buy = signal == "BUY"
    print("Opening position:", signal)

    # Market Open
    result = exchange.market_open(SYMBOL, is_buy, sol_size)
    print("ORDER RESULT:", result)

    # Fill price al
    try:
        fill_price = float(result["response"]["data"]["statuses"][0]["filled"]["avgPx"])
        print("Fill Price:", fill_price)
    except:
        print("Fill price alınamadı")
        return

    time.sleep(2)

    # Gerçek size kontrolü
    state = exchange.info.user_state(account.address)
    size = 0.0
    for p in state.get("assetPositions", []):
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))
            break

    if size < 0.05:
        print("Position not found")
        return

    # ================== TP (Trigger) ==================
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_side = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_side = True

    print("TP Trigger Price:", tp_price)
    try:
        tp_result = exchange.order(
            SYMBOL,
            tp_side,
            size,
            tp_price,
            {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}, "reduceOnly": True}
        )
        print("TP RESULT:", tp_result)
        try:
            current_tp_id = tp_result["response"]["data"]["statuses"][0]["resting"]["oid"]
            print("TP OID:", current_tp_id)
        except:
            print("TP OID alınamadı")
    except Exception as e:
        print("TP koyma hatası:", e)

    time.sleep(2)

    # ================== SL (Trigger) ==================
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_side = False
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_side = True

    print("SL Trigger Price:", sl_price)
    try:
        sl_result = exchange.order(
            SYMBOL,
            sl_side,
            size,
            sl_price,
            {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}, "reduceOnly": True}
        )
        print("SL RESULT:", sl_result)
        try:
            current_sl_id = sl_result["response"]["data"]["statuses"][0]["resting"]["oid"]
            print("SL OID:", current_sl_id)
        except:
            print("SL OID alınamadı")
    except Exception as e:
        print("SL koyma hatası:", e)

    current_position = signal
    print(f"✅ {signal} pozisyonu + TP + SL kuruldu.\n")


def close_position():
    global current_position
    if current_position is None:
        return
    print("Closing position...")
    try:
        exchange.market_close(SYMBOL)
        print("Market close gönderildi")
    except Exception as e:
        print("Close error:", e)
    current_position = None


def process_signal(signal):
    global current_position
    print("SIGNAL RECEIVED:", signal)

    # Eski TP ve SL'leri sil
    cancel_tp()
    cancel_sl()
    time.sleep(2)

    # Ters sinyal varsa pozisyonu kapat
    if current_position and current_position != signal:
        close_position()
        time.sleep(2.5)

    open_position(signal)


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    signal = body.get("signal")
    print("WEBHOOK SIGNAL:", signal)
    
    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}
    
    threading.Thread(target=process_signal, args=(signal,), daemon=True).start()
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive", "current_position": current_position}
