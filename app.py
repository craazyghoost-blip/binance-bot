import os
import time
import threading
import requests
import pandas as pd
import ta

from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ===== CONFIG =====
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "SOL"
POSITION_PERCENT = 0.97
TP_PERCENT = 0.003   # %0.3
MIN_ORDER_USD = 10
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
current_tp_id = None

# ===== INDICATORS (BYBIT) =====
def get_indicators(symbol="SOL"):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}USDT&interval=1&limit=150"

    for i in range(3):  # 🔥 3 kez dene
        try:
            res = requests.get(url, timeout=3)

            if res.status_code != 200:
                print("❌ HTTP hata:", res.status_code)
                time.sleep(1)
                continue

            data = res.json()

            if "result" not in data or "list" not in data["result"]:
                print("❌ Veri format hatası:", data)
                time.sleep(1)
                continue

            df = pd.DataFrame(data["result"]["list"])

            df[1] = df[1].astype(float)
            df[2] = df[2].astype(float)
            df[3] = df[3].astype(float)
            df[4] = df[4].astype(float)
            df[5] = df[5].astype(float)

            df = df[::-1]

            close = df[4]
            high = df[2]
            low = df[3]
            volume = df[5]

            print("✅ Bybit veri OK")

            return {
                "price": close.iloc[-1],
                "rsi": ta.momentum.RSIIndicator(close).rsi().iloc[-1],
                "macd": ta.trend.MACD(close).macd_diff().iloc[-1],
                "adx": ta.trend.ADXIndicator(high, low, close).adx().iloc[-1],
                "ema50": ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1],
                "ema200": ta.trend.EMAIndicator(close, window=200).ema_indicator().iloc[-1],
                "atr": ta.volatility.AverageTrueRange(high, low, close).average_true_range().iloc[-1],
                "volume": volume.iloc[-1],
                "vol_avg": volume.rolling(20).mean().iloc[-1]
            }

        except Exception as e:
            print(f"❌ Deneme {i+1} başarısız:", e)
            time.sleep(1)

    # 🔥 3 deneme de başarısız
    print("❌ Tüm denemeler başarısız → veri yok")
    return None


# ===== FILTER =====
def filter_signal(signal, d):

    price = d["price"]
    rsi = d["rsi"]
    macd = d["macd"]
    adx = d["adx"]
    ema50 = d["ema50"]
    atr = d["atr"]
    volume = d["volume"]
    vol_avg = d["vol_avg"]

    score = 0

    print("\n===== FILTER ANALYSIS =====")

    if adx > 20:
        print(f"ADX: {adx} → +1")
        score += 1
    else:
        print(f"ADX: {adx} → -1")

    if atr > price * 0.002:
        print(f"ATR: {atr} → +1")
        score += 1
    else:
        print(f"ATR: {atr} → -1")

    if volume > vol_avg:
        print(f"VOLUME: {volume} > {vol_avg} → +1")
        score += 1
    else:
        print(f"VOLUME: {volume} < {vol_avg} → -1")

    if signal == "BUY":
        if price > ema50:
            print("EMA → +1")
            score += 1
        else:
            print("EMA → -1")

        if macd > 0:
            print("MACD → +1")
            score += 1
        else:
            print("MACD → -1")

        if 40 < rsi < 65:
            print("RSI → +1")
            score += 1
        else:
            print("RSI → -1")

    if signal == "SELL":
        if price < ema50:
            print("EMA → +1")
            score += 1
        else:
            print("EMA → -1")

        if macd < 0:
            print("MACD → +1")
            score += 1
        else:
            print("MACD → -1")

        if 35 < rsi < 60:
            print("RSI → +1")
            score += 1
        else:
            print("RSI → -1")

    print("TOTAL SCORE:", score)

    return score >= 3


# ===== CORE =====
def format_price(p):
    return round(p, 2)


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def cancel_tp():
    global current_tp_id

    if current_tp_id:
        try:
            exchange.cancel(SYMBOL, current_tp_id)
            print("TP CANCELLED")
        except Exception as e:
            print("TP cancel error:", e)

    current_tp_id = None


def open_position(signal):
    global current_position, current_tp_id

    account_value = get_account_value()
    usd_size = max(account_value * POSITION_PERCENT, MIN_ORDER_USD)

    price = float(exchange.info.all_mids()[SYMBOL])
    size = round(max(usd_size / price, MIN_ORDER_USD / price), 2)

    print("OPEN:", signal, "SIZE:", size)

    result = exchange.market_open(SYMBOL, signal == "BUY", size)

    try:
        fill_price = float(result["response"]["data"]["statuses"][0]["filled"]["avgPx"])
    except:
        print("Fill price alınamadı")
        return

    time.sleep(2)

    state = exchange.info.user_state(account.address)

    pos_size = 0
    for p in state["assetPositions"]:
        if p["position"]["coin"] == SYMBOL:
            pos_size = abs(float(p["position"]["szi"]))

    if pos_size == 0:
        return

    if signal == "BUY":
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        is_buy = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        is_buy = True

    tp = exchange.order(SYMBOL, is_buy, pos_size, tp_price, {"limit": {"tif": "Gtc"}})

    try:
        current_tp_id = tp["response"]["data"]["statuses"][0]["resting"]["oid"]
    except:
        pass

    current_position = signal


def close_position():
    global current_position

    if current_position:
        exchange.market_close(SYMBOL)
        current_position = None


# ===== SIGNAL =====
def process_signal(signal):
    global current_position

    print("SIGNAL:", signal)

    if current_position == signal:
        print("Same → skip")
        return

    data = get_indicators(SYMBOL)

    if data is None:
        print("❌ Veri yok")
        return

    if not filter_signal(signal, data):
        print("❌ Filter reject")
        return

    cancel_tp()
    time.sleep(1)

    if current_position and current_position != signal:
        close_position()
        time.sleep(1)

    open_position(signal)


# ===== WEBHOOK =====
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    signal = body.get("signal")

    if signal not in ["BUY", "SELL"]:
        return {"status": "ignored"}

    threading.Thread(target=process_signal, args=(signal,), daemon=True).start()
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}
