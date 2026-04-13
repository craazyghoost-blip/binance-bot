import os
import time
import threading
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.error import ClientError

app = FastAPI()

# ================== CONFIG ==================
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "SOL"
POSITION_PERCENT = 0.97
TP_PERCENT = 0.004
SL_PERCENT = 0.006
MIN_ORDER_USD = 15
MIN_SIZE = 0.01
# ===========================================

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
        print("Exchange başlatılıyor...")
        for attempt in range(5):
            try:
                exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")
                print("✅ Exchange başarıyla başlatıldı.")
                return exchange
            except ClientError as e:
                if e.status_code == 429:
                    wait = (2 ** attempt) * 5
                    print(f"❌ 429 Rate Limit! {wait} saniye bekleniyor... (deneme {attempt+1}/5)")
                    time.sleep(wait)
                else:
                    print(f"Exchange hatası: {e}")
                    time.sleep(3)
        raise Exception("Exchange başlatılamadı!")
    return exchange


def format_price(price):
    return float(f"{price:.3f}")


def open_position(signal):
    global current_position, current_tp_id, current_sl_id
    print(f"[{time.strftime('%H:%M:%S')}] SIGNAL İŞLENİYOR: {signal}")

    ex = get_exchange()

    # Account ve Price alma
    try:
        account_value = float(ex.info.user_state(account.address)["marginSummary"]["accountValue"])
        price = float(ex.info.all_mids()[SYMBOL])
    except Exception as e:
        print(f"❌ Hesap/Price alınamadı: {e}")
        return

    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)
    size = max(round(usd_size / price, 3), MIN_SIZE)
    is_buy = signal == "BUY"

    print(f"ACCOUNT: {account_value:.2f} | PRICE: {price} | SIZE: {size} | {'LONG' if is_buy else 'SHORT'}")

    # Market Open
    print("Market order gönderiliyor...")
    try:
        result = ex.market_open(SYMBOL, is_buy, size)
        print("MARKET OPEN RESULT:", result)
    except Exception as e:
        print(f"❌ MARKET OPEN HATA: {type(e).__name__} - {e}")
        return

    # Fill bekle
    print("Fill bekleniyor...")
    fill_price = None
    for _ in range(20):
        try:
            state = ex.info.user_state(account.address)
            for p in state.get("assetPositions", []):
                if p.get("position", {}).get("coin") == SYMBOL:
                    szi = float(p["position"].get("szi", 0))
                    if abs(szi) > 0:
                        fill_price = float(p["position"]["entryPx"])
                        print(f"✅ Fill alındı → Entry: {fill_price}")
                        break
            if fill_price:
                break
        except:
            pass
        time.sleep(0.5)

    if not fill_price:
        print("❌ Fill gelmedi! Pozisyon açılmadı.")
        return

    time.sleep(2)
    current_position = signal

    # ================== TP ==================
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_side = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_side = True

    print(f"TP Trigger → {tp_price}")
    try:
        tp = ex.order(SYMBOL, tp_side, size, tp_price,
                      {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}, "reduceOnly": True})
        print("TP RESULT:", tp)
    except Exception as e:
        print(f"TP hatası: {e}")

    time.sleep(2)

    # ================== SL ==================
    if is_buy:
        sl_price = format_price(fill_price * (1 - SL_PERCENT))
        sl_side = False
    else:
        sl_price = format_price(fill_price * (1 + SL_PERCENT))
        sl_side = True

    print(f"SL Trigger → {sl_price}")
    try:
        sl = ex.order(SYMBOL, sl_side, size, sl_price,
                      {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}, "reduceOnly": True})
        print("SL RESULT:", sl)
    except Exception as e:
        print(f"SL hatası: {e}")

    print("✅ Pozisyon + TP + SL tamamlandı.\n")


def process_signal(signal):
    global current_position
    if current_position and current_position != signal:
        print("Yön değişti, mevcut pozisyon kapatılıyor...")
        try:
            get_exchange().market_close(SYMBOL)
        except:
            pass
        current_position = None
        time.sleep(1)

    open_position(signal)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    signal = data.get("signal")
    print("SIGNAL:", signal)
    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}
    
    threading.Thread(target=process_signal, args=(signal,), daemon=True).start()
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive", "current_position": current_position}
