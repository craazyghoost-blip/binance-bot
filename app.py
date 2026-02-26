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
OFFSET = 0.0005          # 0.0002 çok küçüktü, dolmama riski yüksek → 0.05% yaptım (daha gerçekçi)
TP_PERCENT = 0.005       # %0.1 → %0.5 yaptım (daha mantıklı, istersen eski haline çevir)
ORDER_TIMEOUT = 60
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

# Startup'ta bir kere çekilecek precision bilgileri
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
        sz_decimals = 5  # fallback (BTC genelde 5)

init_precision()

# Fiyatı Hyperliquid'in kabul edeceği temiz formata getir
def format_price(raw_price: float) -> float:
    if sz_decimals is None:
        return round(raw_price, 1)  # fallback
    
    # Perpetual'lar için genelde 1 ondalık basamak + tick size uyumu
    # Basit ve güvenli: 1 ondalığa yuvarla (BTC için yaygın)
    return round(raw_price, 1)

# Size'ı precision'a göre floor (güvenli tarafa yuvarla)
def format_size(raw_size: float) -> float:
    if sz_decimals is None:
        return round(raw_size, 5)
    factor = 10 ** sz_decimals
    return math.floor(raw_size * factor) / factor

# =============================
def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])

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
    state = exchange.info.user_state(account.address)
    positions = state.get("assetPositions", [])
    if not positions:
        return
    
    pos = positions[0]["position"]
    entry_price = float(pos["entryPx"])
    size = abs(float(pos["szi"]))
    
    if signal == "BUY":  # long pozisyon → TP sat
        raw_tp = entry_price * (1 + TP_PERCENT)
        is_buy_tp = False
    else:                # short pozisyon → TP al
        raw_tp = entry_price * (1 - TP_PERCENT)
        is_buy_tp = True
    
    tp_price = format_price(raw_tp)
    
    try:
        exchange.order(
            SYMBOL,
            is_buy_tp,
            size,
            tp_price,
            {"limit": {"tif": "Gtc"}}
        )
        print(f"TP SET: {tp_price} (raw: {raw_tp:.2f})")
    except Exception as e:
        print("TP order hatası:", e)

# =============================
def monitor_fill(signal):
    global current_position, pending_order, current_order_id
    start = time.time()
    while time.time() - start < ORDER_TIMEOUT:
        state = exchange.info.user_state(account.address)
        positions = state.get("assetPositions", [])
        if positions:
            print("POSITION FILLED")
            current_position = signal
            pending_order = False
            current_order_id = None
            set_take_profit(signal)
            return
        time.sleep(3)  # rate limit için 3 sn'ye çıkardım
    print("ORDER TIMEOUT")
    cancel_pending()

# =============================
def open_position(signal):
    global pending_order, current_order_id, current_position
    if pending_order:
        print("Order already pending")
        return

    try:
        account_value = get_account_value()
        usd_size = account_value * POSITION_PERCENT
        mids = exchange.info.all_mids()
        btc_price = float(mids[SYMBOL])
        
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
            print("Hesaplanan size çok küçük")
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
        
        handled = False
        for status in statuses:
            if "resting" in status:
                current_order_id = status["resting"]["oid"]
                pending_order = True
                print(f"RESTING ORDER - oid: {current_order_id}")
                threading.Thread(target=monitor_fill, args=(signal,), daemon=True).start()
                handled = True
                break
            elif "filled" in status:
                print("INSTANT FILL")
                current_order_id = None
                pending_order = False
                current_position = signal
                set_take_profit(signal)
                handled = True
                break
            elif "error" in status:
                print("ORDER ERROR:", status["error"])
                handled = True
                break
        
        if not handled:
            print("Bilinmeyen status yapısı:", statuses)
    
    except Exception as e:
        print("open_position exception:", str(e))

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
        
        is_buy_to_close = szi < 0  # short ise alım ile kapat
        sz_abs = abs(szi)
        
        exchange.order(
            SYMBOL,
            is_buy_to_close,
            sz_abs,
            None,  # market order
            {"market": {"slippage": 0.03}}  # %3 slippage toleransı
        )
        print("POSITION MARKET CLOSED")
        current_position = None
    except Exception as e:
        print("Close position hatası:", e)

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
            close_position()
            time.sleep(1.5)  # kapanışın tamamlanması için biraz bekle
        
        if current_position == signal:
            return {"status": "same_position"}
        
        open_position(signal)
        return {"status": "ok"}
    
    except Exception as e:
        print("Webhook exception:", str(e))
        return {"status": "error"}

@app.get("/")
def root():
    return {"status": "alive"}
