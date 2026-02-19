
import os
import time
from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

api_key = os.environ.get("API_KEY")
api_secret = os.environ.get("API_SECRET")

client = Client(api_key, api_secret)

SYMBOL = "BTCUSDT"
LEVERAGE = 2

last_signal_time = 0

def close_position():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for p in positions:
            amt = float(p['positionAmt'])
            if amt > 0:
                client.futures_create_order(
                    symbol=SYMBOL,
                    side=SIDE_SELL,
                    type=ORDER_TYPE_MARKET,
                    quantity=abs(amt)
                )
            elif amt < 0:
                client.futures_create_order(
                    symbol=SYMBOL,
                    side=SIDE_BUY,
                    type=ORDER_TYPE_MARKET,
                    quantity=abs(amt)
                )
    except Exception as e:
        print("Close error:", e)

@app.route("/webhook", methods=["POST"])
def webhook():
    global last_signal_time

    data = request.json
    signal = data.get("signal")

    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

        close_position()

        qty = 0.001  # İstersen değiştir

        if signal == "long":
            client.futures_create_order(
                symbol=SYMBOL,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

        elif signal == "short":
            client.futures_create_order(
                symbol=SYMBOL,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

        last_signal_time = time.time()

        return jsonify({"status": "ok"})

    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/")
def home():
    return "Bot çalışıyor"
