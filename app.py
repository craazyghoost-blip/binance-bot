import os
import requests
from fastapi import FastAPI
from eth_account import Account
import json
import time

app = FastAPI()

PRIVATE_KEY = os.getenv("API_SECRET")
SYMBOL = os.getenv("SYMBOL")
SIDE = os.getenv("SIDE")
SIZE = float(os.getenv("QUANTITY"))

API_URL = "https://api.hyperliquid.xyz/exchange"

account = Account.from_key(PRIVATE_KEY)

@app.get("/")
def place_order():
    is_buy = True if SIDE == "BUY" else False
    
    order = {
        "type": "market",
        "symbol": SYMBOL,
        "side": "buy" if is_buy else "sell",
        "size": SIZE
    }

    response = requests.post(API_URL, json=order)

    return {"status": response.json()}
