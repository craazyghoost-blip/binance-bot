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
TP_PERCENT = 0.0025
MIN_ORDER_USD = 10
# ==================

if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY not set")

account = Account.from_key(PRIVATE_KEY)
print("BOT ADDRESS:", account.address)

exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_position = None
current_tp_id = None

# ===== INDICATORS =====
def get_indicators(symbol="SOL"):
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}USDT&interval=1&limit=150"
        res = requests.get(url, timeout=5).json()

        # veri kontrol
        if "result" not in res or "list" not in res["result"]:
            print("❌ Bybit veri format hatası:", res)
            return None

        kline = res["result"]["list"]

        df = pd.DataFrame(kline)

        # Bybit format:
        # [timestamp, open, high, low, close, volume, turnover]
        df[0] = df[0].astype(float)
        df[1] = df[1].astype(float)
        df[2] = df[2].astype(float)
        df[3] = df[3].astype(float)
        df[4] = df[4].astype(float)
        df[5] = df[5].astype(float)

        df = df[::-1]  # eski → yeni sırala

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
        print("❌ Bybit veri hatası:", e)
        return None

    df[1] = df[1].astype(float)
    df[2] = df[2].astype(float)
    df[3] = df[3].astype(float)
    df[4] = df[4].astype(float)
    df[5] = df[5].astype(float)

    close = df[4]
    high = df[2]
    low = df[3]
    volume = df[5]

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

# ===== FILTER (SCORING) =====
def filter_signal(signal, d):

    price = d["price"]
    rsi = d["rsi"]
    macd = d["macd"]
    adx = d["adx"]
    ema50 = d["ema50"]
    ema200 = d["ema200"]
    atr = d["atr"]
    volume = d["volume"]
    vol_avg = d["vol_avg"]

    score = 0

    print("\n===== FILTER ANALYSIS =====")

    # ADX
    if adx > 20:
        print(f"ADX: {adx} → +1")
        score += 1
    else:
        print(f"ADX: {adx} → -1")

    # ATR
    if atr > price * 0.002:
        print(f"ATR: {atr} → +1")
        score += 1
    else:
        print(f"ATR: {atr} → -1")

    # VOLUME
    if volume > vol_avg:
        print(f"VOLUME: {volume} > {vol_avg} → +1")
        score += 1
    else:
        print(f"VOLUME: {volume} < {vol_avg} → -1")

    # ===== LONG =====
    if signal == "BUY":

        if price > ema50:
            print(f"EMA: price {price} > ema50 {ema50} → +1")
            score += 1
        else:
            print(f"EMA: price {price} < ema50 {ema50} → -1")

        if macd > 0:
            print(f"MACD: {macd} → +1")
            score += 1
        else:
            print(f"MACD: {macd} → -1")

        if 40 < rsi < 65:
            print(f"RSI: {rsi} → +1")
            score += 1
        else:
            print(f"RSI: {rsi} → -1")

    # ===== SHORT =====
    if signal == "SELL":

        if price < ema50:
            print(f"EMA: price {price} < ema50 {ema50} → +1")
            score += 1
        else:
            print(f"EMA: price {price} > ema50 {ema50} → -1")

        if macd < 0:
            print(f"MACD: {macd} → +1")
            score += 1
        else:
            print(f"MACD: {macd} → -1")

        if 35 < rsi < 60:
            print(f"RSI: {rsi} → +1")
            score += 1
        else:
            print(f"RSI: {rsi} → -1")

    print(f"TOTAL SCORE: {score}")

    if score >= 3:
        print("✅ APPROVED\n")
        return True
    else:
        print("❌ REJECTED\n")
        return False

# ===== CORE =====
def format_price(raw_price: float) -> float:
    return round(raw_price, 2)


def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


def cancel_tp():
    global current_tp_id

    if current_tp_id is None:
        return

    try:
        exchange.cancel(SYMBOL, current_tp_id)
        print("TP CANCELLED:", current_tp_id)
        time.sleep(2)
    except Exception as e:
        print("TP cancel error:", e)

    current_tp_id = None


def open_position(signal):
    global current_position
    global current_tp_id

    account_value = get_account_value()
    usd_size = account_value * POSITION_PERCENT

    mids = exchange.info.all_mids()
    sol_price = float(mids[SYMBOL])

    usd_size = max(usd_size, MIN_ORDER_USD)

    raw_size = usd_size / sol_price
    min_size = MIN_ORDER_USD / sol_price

    sol_size = max(round(raw_size, 2), round(min_size, 2))

    print("Opening position:", signal)

    result = exchange.market_open(SYMBOL, signal == "BUY", sol_size)

    try:
        fill_price = float(
            result["response"]["data"]["statuses"][0]["filled"]["avgPx"]
        )
    except:
        print("Fill price alınamadı")
        return

    time.sleep(2)

    state = exchange.info.user_state(account.address)

    size = 0
    for p in state["assetPositions"]:
        if p["position"]["coin"] == SYMBOL:
            size = abs(float(p["position"]["szi"]))

    if size == 0:
        print("Position not found")
        return

    if signal == "BUY":
        tp_price = format_price(fill_price * (1 + TP_PERCENT))
        tp_is_buy = False
    else:
        tp_price = format_price(fill_price * (1 - TP_PERCENT))
        tp_is_buy = True

    tp_result = exchange.order(
        SYMBOL,
        tp_is_buy,
        size,
        tp_price,
        {"limit": {"tif": "Gtc"}}
    )

    try:
        current_tp_id = tp_result["response"]["data"]["statuses"][0]["resting"]["oid"]
    except:
        pass

    current_position = signal


def close_position():
    global current_position

    if current_position is None:
        return

    exchange.market_close(SYMBOL)
    current_position = None


# ===== SIGNAL PROCESS =====
def process_signal(signal):
    global current_position

    print("SIGNAL:", signal)

    if current_position == signal:
        print("Same direction → skip")
        return

    data = get_indicators(SYMBOL)

    if data is None:
        print("❌ Veri alınamadı → işlem iptal")
        return

    if not filter_signal(signal, data):
        return

    cancel_tp()
    time.sleep(2)

    if current_position and current_position != signal:
        close_position()
        time.sleep(2)

    open_position(signal)

# ===== WEBHOOK =====
@app.post("/webhook")
async def webhook(request: Request):

    body = await request.json()
    signal = body.get("signal")

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
