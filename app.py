import os
import time
import requests
from fastapi import FastAPI, Request

app = FastAPI()

PRIVATE_KEY = os.getenv("API_SECRET")
SYMBOL = "BTC"
LEVERAGE = 2

current_position = "NONE"   # NONE / LONG / SHORT
entry_time = None

def get_account_value():
    r = requests.post("https://api.hyperliquid.xyz/info", json={
        "type": "marginSummary"
    })
    data = r.json()
    return float(data["accountValue"])

def get_btc_price():
    r = requests.post("https://api.hyperliquid.xyz/info", json={
        "type": "metaAndAssetCtxs"
    })
    data = r.json()
    return float(data[1][0]["markPx"])

def calculate_size():
    balance = get_account_value()
    price = get_btc_price()
    usable = balance * 0.90
    position_value = usable * LEVERAGE
    size = position_value / price
    return round(size, 4)

def place_market_order(is_buy, reduce_only=False):
    size = calculate_size()

    order = {
        "type": "order",
        "orders": [{
            "coin": SYMBOL,
            "isBuy": is_buy,
            "sz": size,
            "reduceOnly": reduce_only,
            "orderType": {"market": {}}
        }]
    }

    r = requests.post("https://api.hyperliquid.xyz/exchange", json=order)
    return r.text

@app.post("/webhook")
async def webhook(req: Request):
    global current_position, entry_time

    data = await req.json()
    signal = data.get("signal")

    now = time.time()

    # Timeout kontrolü
    if current_position != "NONE" and entry_time:
        if now - entry_time >= 300:
            if current_position == "LONG":
                place_market_order(False, reduce_only=True)
            if current_position == "SHORT":
                place_market_order(True, reduce_only=True)
            current_position = "NONE"

    if signal == "BUY":
        if current_position == "NONE":
            place_market_order(True)
            current_position = "LONG"
            entry_time = now

        elif current_position == "SHORT":
            place_market_order(True, reduce_only=True)
            place_market_order(True)
            current_position = "LONG"
            entry_time = now

    elif signal == "SELL":
        if current_position == "NONE":
            place_market_order(False)
            current_position = "SHORT"
            entry_time = now

        elif current_position == "LONG":
            place_market_order(False, reduce_only=True)
            place_market_order(False)
            current_position = "SHORT"
            entry_time = now

    return {"status": "ok"}
