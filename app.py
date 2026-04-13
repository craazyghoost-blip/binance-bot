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
MIN_ORDER_USD = 15
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = None
current_position = None
current_tp_id = None
current_sl_id = None


def get_exchange():
    global exchange
    if exchange is None:
        print("🔄 Exchange başlatılıyor...")
        exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")
        print("✅ Exchange başarıyla başlatıldı.")
    return exchange


def format_price(raw_price: float) -> float:
    return round(raw_price, 3)


def cancel_tp_sl():
    global current_tp_id, current_sl_id
    if current_tp_id or current_sl_id:
        ex = get_exchange()
        if current_tp_id:
            try:
                ex.cancel(SYMBOL, current_tp_id)
                print("✅ TP CANCELLED")
            except:
                pass
            current_tp_id = None
        if current_sl_id:
            try:
                ex.cancel(SYMBOL, current_sl_id)
                print("✅ SL CANCELLED")
            except:
                pass
            current_sl_id = None


def open_position(signal):
    global current_position, current_tp_id, current_sl_id
    print(f"[{time.strftime('%H:%M:%S')}] → {signal} pozisyonu açılıyor")

    ex = get_exchange()

    account_value = float(ex.info.user_state(account.address)["marginSummary"]["accountValue"])
    sol_price = float(ex.info.all_mids()[SYMBOL])
    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)
    sol_size = round(usd_size / sol_price, 2)
    if sol_size < 0.1:
        sol_size = 0.1

    print(f"PRICE: {sol_price:.3f} | SIZE: {sol_size} | Direction: {'LONG' if signal == 'BUY' else 'SHORT'}")

    is_buy = signal == "BUY"

    # Market Open
    result = ex.market_open(SYMBOL, is_buy, sol_size)
    print("MARKET OPEN RESULT:", result)

    # Fill price al
    try:
        fill_price = float(result["response"]["data"]["statuses"][0]["filled"]["avgPx"])
        print(f"Fill Price: {fill_price}")
    except Exception as e:
        print("Fill price alınamadı:", e)
        return

    time.sleep(2)

    # Gerçek pozisyon size kontrolü
    state = ex.info.user_state(account.address)
    size = 0.0
    for p in state.get("assetPositions", []):
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))
            break

    if size < 0.05:
        print("Pozisyon açılmadı")
        return

    # TP Trigger
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_side = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_side = True

    print(f"TP Trigger: {tp_price}")
    try:
        tp = ex.order(
            SYMBOL, tp_side, size, tp_price,
            {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}, "reduceOnly": True}
        )
        current_tp_id = tp["response"]["data"]["statuses"][0].get("resting", {}).get("oid")
        print("✅ TP konuldu")
    except Exception as e:
        print("TP hatası:", e)

    time.sleep(2)

    # SL Trigger
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_side = False
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_side = True

    print(f"SL Trigger: {sl_price}")
    try:
        sl = ex.order(
            SYMBOL, sl_side, size, sl_price,
            {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}, "reduceOnly": True}
        )
        current_sl_id = sl["response"]["data"]["statuses"][0].get("resting", {}).get("oid")
        print("✅ SL konuldu")
    except Exception as e:
        print("SL hatası:", e)

    current_position = signal
    print(f"✅ {signal} pozisyonu + TP + SL tamamlandı.\n")


def process_signal(signal):
    global current_position
    print(f"WEBHOOK SIGNAL: {signal} | Mevcut pozisyon: {current_position}")

    cancel_tp_sl()
    time.sleep(2)

    if current_position and current_position != signal:
        print(f"Ters sinyal: {current_position} → {signal} | Pozisyon kapatılıyor...")
        try:
            get_exchange().market_close(SYMBOL)
            print("Market close gönderildi")
        except Exception as e:
            print("Close hatası:", e)
        current_position = None
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
