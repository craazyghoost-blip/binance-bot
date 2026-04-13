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
MIN_ORDER_USD = 15
MIN_SIZE = 0.01
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


def wait_for_fill():
    for _ in range(20):  # max 10 sn bekler
        try:
            state = exchange.info.user_state(account.address)
            for p in state["assetPositions"]:
                if p["position"]["coin"] == SYMBOL:
                    size = float(p["position"]["szi"])
                    if abs(size) > 0:
                        return float(p["position"]["entryPx"])
        except:
            pass
        time.sleep(0.5)
    return None


def get_position_size():
    state = exchange.info.user_state(account.address)
    for p in state["assetPositions"]:
        if p["position"]["coin"] == SYMBOL:
            return abs(float(p["position"]["szi"]))
    return 0


def open_position(signal):
    global current_position, current_tp_id, current_sl_id
    print("Signal geldi, 3 sn bekleniyor...")
    time.sleep(3)

    account_value = get_account_value()
    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)
    price = float(exchange.info.all_mids()[SYMBOL])
    size = max(round(usd_size / price, 3), MIN_SIZE)

    is_buy = signal == "BUY"
    print("ACCOUNT:", account_value)
    print("PRICE:", price)
    print("SIZE:", size)
    print("Pozisyon açılıyor:", signal)

    try:
        result = exchange.market_open(SYMBOL, is_buy, size)
        print("MARKET OPEN RESULT:", result)
    except Exception as e:
        print("ORDER HATA:", e)
        return

    print("Fill bekleniyor...")
    fill_price = wait_for_fill()
    if not fill_price:
        print("Fill gelmedi → işlem açılmadı")
        return

    print("Fill price:", fill_price)
    time.sleep(2)   # artırıldı

    size = get_position_size()
    if size == 0:
        print("Pozisyon yok, işlem iptal")
        return

    # ===== TP (Trigger Order) =====
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_side = False   # Long → Sell to close
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_side = True    # Short → Buy to close

    print("TP Trigger koyuluyor... TP Price:", tp_price)
    try:
        tp = exchange.order(
            SYMBOL,
            tp_side,
            size,
            tp_price,
            {
                "trigger": {
                    "triggerPx": tp_price,
                    "isMarket": True,
                    "tpsl": "tp"
                },
                "reduceOnly": True
            }
        )
        print("TP ORDER RESULT:", tp)
        
        try:
            current_tp_id = tp["response"]["data"]["statuses"][0]["resting"]["oid"]
            print("TP OID:", current_tp_id)
        except:
            current_tp_id = None
    except Exception as e:
        print("TP hata:", e)

    time.sleep(2)

    # ===== SL (Trigger Order) =====
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_side = False   # Long → Sell
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_side = True    # Short → Buy

    print("SL Trigger koyuluyor... SL Price:", sl_price)
    try:
        sl = exchange.order(
            SYMBOL,
            sl_side,
            size,
            sl_price,
            {
                "trigger": {
                    "triggerPx": sl_price,
                    "isMarket": True,
                    "tpsl": "sl"
                },
                "reduceOnly": True
            }
        )
        print("SL ORDER RESULT:", sl)
        
        try:
            current_sl_id = sl["response"]["data"]["statuses"][0]["resting"]["oid"]
            print("SL OID:", current_sl_id)
        except:
            current_sl_id = None
    except Exception as e:
        print("SL hata:", e)

    current_position = signal
    print("Pozisyon + TP + SL başarıyla kuruldu.\n")


def close_position():
    global current_position
    if current_position:
        print("Pozisyon kapatılıyor...")
        try:
            exchange.market_close(SYMBOL)
        except Exception as e:
            print("Close hata:", e)
        
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
