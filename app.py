import os
from fastapi import FastAPI
from hyperliquid.exchange import Exchange

app = FastAPI()

PRIVATE_KEY = os.getenv("API_SECRET")
SYMBOL = os.getenv("SYMBOL")
SIDE = os.getenv("SIDE")
SIZE = float(os.getenv("QUANTITY"))

exchange = Exchange(PRIVATE_KEY)

@app.get("/")
def place_order():
    is_buy = True if SIDE == "BUY" else False

    exchange.market_open(
        name=SYMBOL,
        is_buy=is_buy,
        sz=SIZE
    )

    return {"status": "order sent"}
