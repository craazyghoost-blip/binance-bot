import os
import time
import threading
import math
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
POSITION_PERCENT = 0.9
OFFSET = 0.0008          # %0.08
TP_PERCENT = 0.01        # %1
ORDER_TIMEOUT = 60
TICK_SIZE = 0.1          # kullanılmıyor artık, ama bırakabilirsin
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

# Global değişkenler
current_position = None
pending_order = False
current_order_id = None

# Startup'ta precision
sz_decimals = None

def init_precision():
    global sz_decimals
    try:
        meta, _ = exchange.info.meta_and_asset_ctxs()
        for asset in meta["universe"]:
            if asset["name"] == SYMBOL:
                sz_decimals = asset["szDecimals"]
                print(f"{SYMBOL} szDecimals: {sz_decimals}")
                return
        raise Exception(f"{SYMBOL} meta'da bulunamadı")
    except Exception as e:
        print("Precision init hatası:", e)
        sz_decimals = 5

init_precision()

# Fiyat formatlama - INTEGER YAPARAK HATAYI ÇÖZÜYORUZ
def format_price(raw_price: float) -> float:
    sig_fig = float(f"{raw_price:.5g}")
    final_price = round(sig_fig)  # tam sayıya yuvarla
    print(f"Raw price: {raw_price:.2f} → formatted (integer): {final_price}")
    return final_price

# Size format aynı
def format_size(raw_size: float) -> float:
    if sz_decimals is None:
        return round(raw_size, 5)
    factor = 10 ** sz_decimals
    return math.floor(raw_size * factor) / factor

# kalan fonksiyonlar (get_account_value, cancel_pending, set_take_profit, monitor_fill, open_position, close_position, webhook, root) TAMAMEN AYNI KALDI – değiştirmedim

# =============================
def get_account_value():
    try:
        state = exchange.info.user_state(account.address)
        return float(state["marginSummary"]["accountValue"])
    except Exception as e:
        print("Account value error:", e)
        return 0.0

# =============================
def cancel_pending():
    global current_order_id, pending_order
    if current_order_id:
        try:
            exchange.cancel(SYMBOL, current_order_id)
            print("LIMIT CANCELLED")
        except Exception as e:
            print("Cancel error:", e)
    current_order_id = None
    pending_order = False

# =============================
def set_take_profit(signal):
    try:
        state = exchange.info.user_state(account.address)
        positions = state.get("assetPositions", [])
        if not positions:
            return
        
        pos = positions[0]["position"]
        entry_price = float(pos["entryPx"])
        size = abs(float(pos["szi"]))
        
        if signal == "BUY":  # long → TP sell
            raw_tp = entry_price * (1 + TP_PERCENT)
            is_buy_tp = False
        else:                # short → TP buy
            raw_tp = entry_price * (1 - TP_PERCENT)
            is_buy_tp = True
        
        tp_price = format_price(raw_tp)
        
        exchange.order(
            SYMBOL,
            is_buy_tp,
            size,
            tp_price,
            {"limit": {"tif": "Gtc"}}
        )
        print(f"TP SET: {tp_price} (raw ~ {raw_tp:.1f})")
    except Exception as e:
        print("TP set error:", e)

# =============================
def monitor_fill(signal):
    global current_position, pending_order, current_order_id
    start = time.time()
    while time.time() - start < ORDER_TIMEOUT:
        try:
            state = exchange.info.user_state(account.address)
            positions = state.get("assetPositions", [])
            if positions:
                print("POSITION FILLED → signal:", signal)
                current_position = signal
                pending_order = False
                current_order_id = None
                set_take_profit(signal)
                return
        except Exception as e:
            print("Monitor state error:", e)
        time.sleep(4)
    print("ORDER TIMEOUT → iptal")
    cancel_pending()

# =============================
def open_position(signal):
    global pending_order, current_order_id, current_position
    if pending_order:
        print("Zaten pending order var, bekle")
        return

    try:
        account_value = get_account_value()
        if account_value <= 0:
            print("Hesap değeri sıfır veya alınamadı")
            return
        usd_size = account_value * POSITION_PERCENT
        
        mids = exchange.info.all_mids()
        btc_price = float(mids.get(SYMBOL, 0))
        if btc_price <= 0:
            print("Mid price alınamadı")
            return
        
        if signal == "BUY":
            is_buy = True
            raw_limit = btc_price * (1 - OFFSET)
        else:
            is_buy = False
            raw_limit = btc_price * (1 + OFFSET)
        
        limit_price = format_price(raw_limit)
        print(f"LIMIT ORDER: {signal} {limit_price} (raw: {raw_limit:.2f})")
        
        raw_btc_size = usd_size / btc_price
        btc_size = format_size(raw_btc_size)
        if btc_size <= 0:
            print("Size çok küçük veya sıfır")
            return
        
        print(f"Size: {btc_size}")
        
        order_result = exchange.order(
            SYMBOL,
            is_buy,
            btc_size,
            limit_price,
            {"limit": {"tif": "Gtc"}}
        )
        
        print("Order response:", order_result)
        
        if order_result.get("status") != "ok":
            print("Order başarısız:", order_result)
            return
        
        statuses = order_result["response"]["data"]["statuses"]
        
        for status in statuses:
            if "resting" in status:
                current_order_id = status["resting"]["oid"]
                pending_order = True
                print(f"RESTING ORDER → oid: {current_order_id}")
                threading.Thread(target=monitor_fill, args=(signal,), daemon=True).start()
                return
            elif "filled" in status:
                print("INSTANT / FILLED")
                current_order_id = None
                pending_order = False
                current_position = signal
                set_take_profit(signal)
                return
            elif "error" in status:
                print("ORDER ERROR:", status["error"])
                return
        
        print("Beklenmeyen status:", statuses)
    
    except Exception as e:
        print("open_position hatası:", str(e))

# =============================
def close_position():
    global current_position
    if current_position is None:
        return
    
    try:
        state = exchange.info.user_state(account.address)
        positions = state.get("assetPositions", [])
        if not positions:
            current_position = None
            return
        
        pos = positions[0]["position"]
        szi = float(pos["szi"])
        if szi == 0:
            current_position = None
            return
        
        is_buy_to_close = szi < 0
        sz_abs = abs(szi)
        
        exchange.order(
            SYMBOL,
            is_buy_to_close,
            sz_abs,
            None,
            {"market": {"slippage": 0.03}}
        )
        print("MARKET CLOSE → pozisyon kapatıldı")
        current_position = None
    except Exception as e:
        print("Close hatası:", e)

# =============================
@app.post("/webhook")
async def webhook(request: Request):
    global current_position
    try:
        body = await request.json()
        signal = body.get("signal")
        print("SIGNAL:", signal)
        
        if signal not in ["BUY", "SELL"]:
            return {"status": "ignored"}
        
        cancel_pending()
        
        if current_position and current_position != signal:
            print(f"Ters sinyal → mevcut {current_position} kapatılıyor")
            close_position()
            time.sleep(2)
        
        if current_position == signal:
            print("Aynı pozisyon zaten açık")
            return {"status": "same_position"}
        
        open_position(signal)
        return {"status": "ok"}
    
    except Exception as e:
        print("Webhook hatası:", str(e))
        return {"status": "error"}

@app.get("/")
def root():
    return {"status": "alive"}
