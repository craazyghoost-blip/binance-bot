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
current_tp_id = None
current_sl_id = None


def get_exchange():
    global exchange
    if exchange is None:
        exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")
    return exchange


def format_price(raw_price: float) -> float:
    return round(raw_price, 3)


# ===== STATE =====
def is_position_open():
    ex = get_exchange()
    state = ex.info.user_state(account.address)

    for p in state.get("assetPositions", []):
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))
            if size > 0:
                return True
    return False


def cancel_all_orders():
    global current_tp_id, current_sl_id
    ex = get_exchange()

    try:
        open_orders = ex.info.open_orders(account.address)
        for o in open_orders:
            if o["coin"] == SYMBOL:
                ex.cancel(SYMBOL, o["oid"])
        print("🧹 Tüm açık emirler silindi")
    except Exception as e:
        print("Cancel error:", e)

    current_tp_id = None
    current_sl_id = None


# ===== MONITOR (OCO SIMULATION) =====
def monitor_position():
    global current_tp_id, current_sl_id

    while True:
        try:
            if not is_position_open():
                if current_tp_id or current_sl_id:
                    print("📴 Pozisyon kapandı → TP/SL temizleniyor")
                    cancel_all_orders()
        except Exception as e:
            print("Monitor error:", e)

        time.sleep(2)


threading.Thread(target=monitor_position, daemon=True).start()


# ===== TRADE =====
def open_position(signal):
    global current_tp_id, current_sl_id

    print(f"{signal} açılıyor")

    ex = get_exchange()

    # önce her şeyi temizle
    cancel_all_orders()

    account_value = float(ex.info.user_state(account.address)["marginSummary"]["accountValue"])
    price = float(ex.info.all_mids()[SYMBOL])

    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)
    size = round(usd_size / price, 2)

    if size < 0.1:
        size = 0.1

    is_buy = signal == "BUY"

    result = ex.market_open(SYMBOL, is_buy, size)

    try:
        fill_price = float(result["response"]["data"]["statuses"][0]["filled"]["avgPx"])
    except:
        print("Fill price alınamadı")
        return

    time.sleep(2)

    # gerçek size
    state = ex.info.user_state(account.address)
    actual_size = 0.0
    for p in state.get("assetPositions", []):
        if p["position"]["coin"] == SYMBOL:
            actual_size = abs(float(p["position"]["szi"]))
            break

    if actual_size < 0.05:
        print("Pozisyon açılmadı")
        return

    # ===== TP =====
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_side = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_side = True

    tp = ex.order(
        SYMBOL, tp_side, actual_size, tp_price,
        {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}, "reduceOnly": True}
    )

    current_tp_id = tp["response"]["data"]["statuses"][0].get("resting", {}).get("oid")
    print("✅ TP konuldu")

    time.sleep(1)

    # ===== SL =====
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_side = False
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_side = True

    sl = ex.order(
        SYMBOL, sl_side, actual_size, sl_price,
        {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}, "reduceOnly": True}
    )

    current_sl_id = sl["response"]["data"]["statuses"][0].get("resting", {}).get("oid")
    print("✅ SL konuldu")

    print("✅ Pozisyon + TP + SL hazır\n")


def process_signal(signal):
    print(f"Sinyal: {signal}")

    # yeni sinyal → önce her şeyi kapat
    cancel_all_orders()

    if is_position_open():
        print("Pozisyon var → önce kapatılıyor")
        ex = get_exchange()
        ex.market_close(SYMBOL)
        time.sleep(2)

    open_position(signal)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    signal = data.get("signal")

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    threading.Thread(target=process_signal, args=(signal,), daemon=True).start()
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive", "position_open": is_position_open()}
