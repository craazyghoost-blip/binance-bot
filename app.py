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
current_position = None   # "BUY" veya "SELL" veya None
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
            except Exception as e:
                print(f"Exchange başlatma hatası (deneme {attempt+1}): {e}")
                time.sleep(5 if "429" in str(e) else 3)
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
            print(f"✅ Eski TP iptal edildi")
        except:
            pass
        current_tp_id = None
    if current_sl_id:
        try:
            ex.cancel(SYMBOL, current_sl_id)
            print(f"✅ Eski SL iptal edildi")
        except:
            pass
        current_sl_id = None


def close_position_fully():
    global current_position
    print("🔴 Mevcut pozisyon kapatılıyor...")
    cancel_tp_sl()

    ex = get_exchange()
    for i in range(4):
        try:
            ex.market_close(SYMBOL)
            print(f"Kapatma denemesi {i+1}")
            time.sleep(2)
            
            # Pozisyon kontrolü
            state = ex.info.user_state(account.address)
            size = 0.0
            for p in state.get("assetPositions", []):
                if p.get("position", {}).get("coin") == SYMBOL:
                    size = abs(float(p["position"].get("szi", 0)))
            if size < 0.1:
                print("✅ Pozisyon tamamen kapatıldı.")
                current_position = None
                return
        except Exception as e:
            print(f"Close hatası: {e}")
    print("⚠️ Pozisyon kapatma tamamlandı (kontrol edilemedi).")
    current_position = None


def open_position(signal):
    global current_position, current_tp_id, current_sl_id
    print(f"[{time.strftime('%H:%M:%S')}] → {signal} sinyali işleniyor")

    ex = get_exchange()

    try:
        state = ex.info.user_state(account.address)
        account_value = float(state["marginSummary"]["accountValue"])
        price = float(ex.info.all_mids()[SYMBOL])
    except Exception as e:
        print(f"❌ Account/Price alınamadı: {e}")
        return

    size = round(max(account_value * POSITION_PERCENT, MIN_ORDER_USD) / price, 2)
    if size < 0.1:
        size = 0.1

    is_buy = signal == "BUY"
    print(f"ACCOUNT: {account_value:.2f} | PRICE: {price:.3f} | SIZE: {size} | {'LONG' if is_buy else 'SHORT'}")

    print("🚀 Market order gönderiliyor...")
    try:
        result = ex.market_open(SYMBOL, is_buy, size)
        print("MARKET OPEN RESULT:", result)
    except Exception as e:
        print(f"❌ MARKET OPEN HATA: {e}")
        traceback.print_exc()
        return

    # Daha toleranslı fill kontrolü
    print("Fill bekleniyor (max 12sn)...")
    fill_price = None
    for _ in range(24):
        try:
            state = ex.info.user_state(account.address)
            for p in state.get("assetPositions", []):
                if p.get("position", {}).get("coin") == SYMBOL:
                    szi = float(p["position"].get("szi", 0))
                    if abs(szi) > 0.05:
                        fill_price = float(p["position"]["entryPx"])
                        actual_size = abs(szi)
                        print(f"✅ Fill alındı → Entry: {fill_price} | Size: {actual_size}")
                        break
            if fill_price:
                break
        except:
            pass
        time.sleep(0.5)

    if not fill_price:
        print("❌ Fill tespit edilemedi! (Ama order filled olabilir)")
        # Yine de devam et, belki pozisyon var
        time.sleep(2)

    time.sleep(2)

    # TP ve SL koy
    if is_buy:
        tp_price = format_price(fill_price * (1 + TP_PERCENT) if fill_price else price * (1 + TP_PERCENT))
        tp_side = False
        sl_price = format_price(fill_price * (1 - SL_PERCENT) if fill_price else price * (1 - SL_PERCENT))
        sl_side = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT) if fill_price else price * (1 - TP_PERCENT))
        tp_side = True
        sl_price = format_price(fill_price * (1 + SL_PERCENT) if fill_price else price * (1 + SL_PERCENT))
        sl_side = True

    print(f"TP Trigger → {tp_price} | SL Trigger → {sl_price}")

    try:
        tp = ex.order(SYMBOL, tp_side, size, tp_price, {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}, "reduceOnly": True})
        current_tp_id = tp.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
    except Exception as e:
        print(f"TP hatası: {e}")

    time.sleep(2)

    try:
        sl = ex.order(SYMBOL, sl_side, size, sl_price, {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}, "reduceOnly": True})
        current_sl_id = sl.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid")
    except Exception as e:
        print(f"SL hatası: {e}")

    current_position = signal
    print(f"✅ {signal} pozisyonu tamamlandı.\n")


def process_signal(signal):
    global current_position
    print(f"Sinyal alındı: {signal} | Mevcut pozisyon: {current_position}")

    if current_position and current_position != signal:
        print(f"🔄 TERS SİNYAL → Eski pozisyon kapatılıyor ({current_position} → {signal})")
        close_position_fully()
        time.sleep(3)   # önemli: ters işlem öncesi bekleme

    open_position(signal)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    signal = data.get("signal")
    print("WEBHOOK SIGNAL:", signal)
    
    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}
    
    threading.Thread(target=process_signal, args=(signal,), daemon=True).start()
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive", "current_position": current_position}
