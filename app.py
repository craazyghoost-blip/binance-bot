import os
import time
import threading
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

app = FastAPI()

# ================= CONFIG =================
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SYMBOL = "BTC"
POSITION_PERCENT = 0.9

TP1_PERCENT = 0.50
TP2_PERCENT = 0.30
TP3_PERCENT = 0.20

SIGNAL_COOLDOWN = 3  # saniye
# ===========================================

account = Account.from_key(PRIVATE_KEY)
exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")

current_side = None
current_size = 0

last_signal = None
last_signal_time = 0


# ===========================================
def get_account_value():
    state = exchange.info.user_state(account.address)
    return float(state["marginSummary"]["accountValue"])


# ===========================================
def open_position(side):

    global current_side, current_size

    value = get_account_value()

    mids = exchange.info.all_mids()
    price = float(mids["BTC"])

    usd = value * POSITION_PERCENT
    size = round(usd / price, 5)

    print("OPEN:", side)

    exchange.market_open(
        SYMBOL,
        side == "BUY",
        size
    )

    current_side = side
    current_size = size


# ===========================================
def close_all():

    global current_side, current_size

    if current_side:
        print("FULL CLOSE")
        exchange.market_close(SYMBOL)

    current_side = None
    current_size = 0


# ===========================================
def partial_close(percent):

    global current_size, current_side

    if current_size <= 0:
        return

    size = round(current_size * percent, 5)

    print("TP CLOSE:", size)

    exchange.market_open(
        SYMBOL,
        current_side != "BUY",
        size
    )

    current_size -= size


# ===========================================
def handle_signal(msg):

    global last_signal, last_signal_time
    global current_side

    now = time.time()
    msg = msg.strip().upper()

    # ===== SPAM LOCK =====
    if msg == last_signal and now - last_signal_time < SIGNAL_COOLDOWN:
        print("SPAM BLOCKED:", msg)
        return

    last_signal = msg
    last_signal_time = now

    print("SIGNAL:", msg)

    # ===== ENTRY =====
    if msg == "LE":

        if current_side == "BUY":
            return

        close_all()
        open_position("BUY")

    elif msg == "SE":

        if current_side == "SELL":
            return

        close_all()
        open_position("SELL")

    # ===== TAKE PROFITS =====
    elif msg in ["LXTP1", "SXTP1"]:
        partial_close(TP1_PERCENT)

    elif msg in ["LXTP2", "SXTP2"]:
        partial_close(TP2_PERCENT)

    elif msg in ["LXTP3", "SXTP3"]:
        partial_close(TP3_PERCENT)

    # ===== STOP / EXIT =====
    elif msg in ["LX", "SX", "SL"]:
        close_all()


# ===========================================
@app.post("/webhook")
async def webhook(request: Request):

    body = await request.body()
    message = body.decode()

    threading.Thread(
        target=handle_signal,
        args=(message,),
        daemon=True
    ).start()

    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "alive"}


# ===========================================
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
