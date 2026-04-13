import os
import time
import threading
import traceback
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
        for attempt in range(6):
            try:
                exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")
                print("✅ Exchange başarıyla başlatıldı.")
                return exchange
            except ClientError as e:
                if e.status_code == 429:
                    wait = (2 ** attempt) * 6
                    print(f"❌ 429 Rate Limit! {wait} saniye bekleniyor...")
                    time.sleep(wait)
                else:
                    print(f"Exchange başlatma hatası: {e}")
                    time.sleep(4)
            except Exception as e:
                print(f"Beklenmeyen exchange hatası: {e}")
                time.sleep(4)
        raise Exception("Exchange başlatılamadı!")
    return exchange


def format_price(price):
    return float(f"{price:.3f}")


def cancel_tp_sl():
    global current_tp_id, current_sl_id
    ex = get_exchange()
    if current_tp_id:
        try:
            ex.cancel(SYMBOL, current_tp_id)
            print(f"✅ Eski TP iptal edildi (ID: {current_tp_id})")
        except:
            print("TP iptal edilemedi")
        current_tp_id = None

    if current_sl_id:
        try:
            ex.cancel(SYMBOL, current_sl_id)
            print(f"✅ Eski SL iptal edildi (ID: {current_sl_id})")
        except:
            print("SL iptal edilemedi")
        current_sl_id = None


def close_position_fully():
    global current_position
    print("🔴 Mevcut pozisyon kapatılıyor...")
    cancel_tp_sl()

    ex = get_exchange()
    for attempt in range(4):
        try:
            print(f"Kapatma denemesi {attempt+1}/4")
            ex.market_close(SYMBOL)
            time.sleep(1.8)

            # Kontrol
            state = ex.info.user_state(account.address)
            size = 0
            for p in state.get("assetPositions", []):
                if p.get("position", {}).get("coin") == SYMBOL:
                    size = abs(float(p["position"].get("szi", 0)))
            if size < 0.05:
                print("✅ Pozisyon tamamen kapatıldı.")
                current_position = None
                return True
        except Exception as e:
            print(f"Close hatası: {e}")
        time.sleep(1)

    print("⚠️ Pozisyon tam kapanmadı.")
    current_position = None
    return False


def open_position(signal):
    global current_position, current_tp_id, current_sl_id
    print(f"[{time.strftime('%H:%M:%S')}] SIGNAL İŞLENİYOR → {signal}")

    ex = get_exchange()

    try:
        account_value = float(ex.info.user_state(account.address)["marginSummary"]["accountValue"])
        price = float(ex.info.all_mids()[SYMBOL])
    except Exception as e:
        print(f"❌ Hesap/Price alınamadı: {e}")
        traceback.print_exc()
        return

    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)
    size = round(usd_size / price, 2)
    if size < 0.1:
        size = 0.1

    is_buy = signal == "BUY"
    print(f"ACCOUNT: {account_value:.2f} | PRICE: {price:.3f} | SIZE: {size} | {'LONG' if is_buy else 'SHORT'}")

    print("🚀 Market order gönderiliyor...")
    try:
        result = ex.market_open(SYMBOL, is_buy, size)
        print("MARKET OPEN RESULT:", result)
    except Exception as e:
        print(f"❌ MARKET OPEN HATA: {type(e).__name__} - {e}")
        traceback.print_exc()
        return

    print("Fill bekleniyor...")
    fill_price = None
    for _ in range(25):   # biraz daha uzun bekle
        try:
            state = ex.info.user_state(account.address)
            for p in state.get("assetPositions", []):
                if p.get("position", {}).get("coin") == SYMBOL:
                    szi = abs(float(p["position"].get("szi", 0)))
                    if szi > 0.05:
                        fill_price = float(p["position"]["entryPx"])
                        print(f"✅ Fill alındı → Entry: {fill_price} | Size: {szi}")
                        break
            if fill_price:
                break
        except Exception as e:
            print(f"User state kontrol hatası: {e}")
        time.sleep(0.5)

    if not fill_price:
        print("❌ Fill gelmedi! İşlem iptal edildi.")
        return

    time.sleep(2)

    # TP
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
        current_tp_id = tp.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
        print("✅ TP konuldu")
    except Exception as e:
        print(f"TP hatası: {e}")

    time.sleep(2)

    # SL
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
        current_sl_id = sl.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
        print("✅ SL konuldu")
    except Exception as e:
        print(f"SL hatası: {e}")

    current_position = signal
    print(f"✅ {signal} pozisyonu + TP + SL kuruldu.\n")


def process_signal(signal):
    global current_position

    if current_position is not None and current_position != signal:
        print(f"🔄 TERS SİNYAL ALINDI: {current_position} → {signal}")
        close_position_fully()
        time.sleep(2.5)   # ters işlem öncesi ekstra bekleme

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
