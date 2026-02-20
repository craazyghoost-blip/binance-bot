import os
from fastapi import FastAPI
from hyperliquid.client import Client

app = FastAPI()

PRIVATE_KEY = os.getenv("API_SECRET")
SYMBOL = os.getenv("SYMBOL")
SIDE = os.getenv("SIDE")
SIZE = float(os.getenv("QUANTITY"))

client = Client(private_key=PRIVATE_KEY)

@app.get("/")
def place_order():
    is_buy = True if SIDE == "BUY" else False

    client.market_open(
        name=SYMBOL,
        is_buy=is_buy,
        sz=SIZE
    )

    return {"status": "order sent"}
