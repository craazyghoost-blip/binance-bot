from fastapi import FastAPI
from pydantic import BaseModel
import time

app = FastAPI()

last_signal = None
last_trade_time = 0

class WebhookData(BaseModel):
    signal: str

@app.post("/webhook")
async def webhook(data: WebhookData):
    global last_signal, last_trade_time

    signal = data.signal.upper()

    if signal not in ["BUY", "SELL"]:
        return {"status": "invalid signal"}

    now = time.time()

    if signal == last_signal:
        return {"status": "duplicate ignored"}

    last_signal = signal
    last_trade_time = now

    print("SIGNAL RECEIVED:", signal)

    return {"status": "ok"}
