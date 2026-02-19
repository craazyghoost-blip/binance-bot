import time
from flask import Flask, request
from binance.client import Client
from binance.enums import *
import os

API_KEY = os.environ.get("API_KEY")
API_SECRET = os.environ.get("API_SECRET")

client = Client(API_KEY, API_SECRET)
client.futures_change_leverage(symbol="BTCUSDT", leverage=2)

app = Flask(__name__)

def get_position():
    positions = client.futures_position_information(symbol="BTCUSDT")
    for p in positions:
        if float(p["positionAmt"]) != 0:
            return p
    return None

def close_position():
    pos = get_position()
    if pos:
        amt = abs(float(pos["positionAmt"]))
        side = SIDE_SELL if float(pos["positionAmt"]) > 0 else SIDE_BUY
        client.futures_create_order(
            symbol="BTCUSDT",
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=amt
        )

def open_position(direction):
    balance = float(client.futures_account_balance()[6]['balance'])
    price = float(client.futures_mark_price(symbol="BTCUSDT")['markPrice'])
    qty = round((balance * 0.95 * 2) / price, 3)

    side = SIDE_BUY if direction == "long" else SIDE_SELL

    client.futures_create_order(
        symbol="BTCUSDT",
        side=side,
        type=ORDER_TYPE_MARKET,
        quantity=qty
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    signal = data.get("signal")

    if signal == "long":
        close_position()
        time.sleep(0.5)
        open_position("long")

    elif signal == "short":
        close_position()
        time.sleep(0.5)
        open_position("short")

    elif signal == "close":
        close_position()

    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
