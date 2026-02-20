import os
import requests
from fastapi import FastAPI

app = FastAPI()

SYMBOL = os.getenv("SYMBOL")
SIDE = os.getenv("SIDE")
SIZE = float(os.getenv("QUANTITY"))

API_URL = "https://api.hyperliquid.xyz/info"

@app.get("/")
def place_order():
    order = {
        "type": "market",
        "symbol": SYMBOL,
        "side": "buy" if SIDE == "BUY" else "sell",
        "size": SIZE
    }

    r = requests.post(API_URL, json=order)

    return {"response": r.text}
