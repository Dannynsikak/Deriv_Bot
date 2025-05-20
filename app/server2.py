import os
import json
import time
from datetime import datetime
import websocket
from dotenv import load_dotenv

load_dotenv()

# ===== CONFIGURATION =====
API_TOKEN = os.getenv("DERIV_API_TOKEN", "**********Go2Hj")  # Load from env var
App_ID = os.getenv("APP_ID")
if not App_ID:
    raise EnvironmentError("Please set the APP_ID environment variable.")
SYMBOL = "frxEURUSD"
STOP_LOSS = -1.0  # USD
TAKE_PROFIT = 2.0  # USD
STAKE_AMOUNT = 1  # USD per trade
CONTRACT_DURATION = 5  # Ticks
PRICE_MOVEMENT_THRESHOLD = 0.0002  # 0.02%

# ===== GLOBALS =====
active_contract_id = None
previous_tick = None
should_reconnect = True

# ===== LOGGING =====
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)
    with open("bot_logs.txt", "a") as f:
        f.write(log_entry + "\n")

# ===== WEBSOCKET HANDLERS =====
def on_open(ws):
    log("WebSocket connected. Authenticating...")
    auth_msg = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_msg))

def on_message(ws, message):
    global active_contract_id, previous_tick

    data = json.loads(message)

   # Check for authorization response
    if data.get('msg_type') == 'authorize':
        log("Authorization successful. Subscribing to ticks...")
        subscribe_to_ticks(ws)
        return

    elif data.get('error'):
        log(f"Authorization failed: {data['error']['message']}")
        ws.close()
        return

    # Process tick data
    elif "tick" in data:
        price = float(data["tick"]["quote"])
        log(f"Tick: {price}")

        if previous_tick is None:
            previous_tick = price
            return

        signal = analyze_market(price)
        if signal and not active_contract_id:
            log(f"Signal detected: {signal.upper()} - Placing trade...")
            place_order(ws, signal)

    # Handle trade opening
    elif "buy" in data and data["buy"].get("contract_id"):
        active_contract_id = data["buy"]["contract_id"]
        log(f"Trade opened! Contract ID: {active_contract_id}")

    # Monitor profit/loss
    elif "contract_update" in data and active_contract_id:
        monitor_profit_loss(ws, data)

def on_error(ws, error):
    log(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    log(f"Connection closed. Code: {close_status_code}, Reason: {close_msg}")
    if should_reconnect:
        log("Attempting to reconnect in 5 seconds...")
        time.sleep(5)
        start_bot()

# ===== TRADING LOGIC =====
def subscribe_to_ticks(ws):
    sub_msg = {"ticks": SYMBOL, "subscribe": 1}
    ws.send(json.dumps(sub_msg))

def analyze_market(current_price):
    global previous_tick

    if current_price > previous_tick * (1 + PRICE_MOVEMENT_THRESHOLD):
        return "buy"
    elif current_price < previous_tick * (1 - PRICE_MOVEMENT_THRESHOLD):
        return "sell"
    previous_tick = current_price
    return None

def place_order(ws, action):
    order = {
        "buy": 1 if action == "buy" else 0,
        "price": 0,
        "parameters": {
            "amount": STAKE_AMOUNT,
            "basis": "stake",
            "contract_type": "CALL" if action == "buy" else "PUT",
            "currency": "USD",
            "duration": CONTRACT_DURATION,
            "duration_unit": "t",
            "symbol": SYMBOL
        }
    }
    ws.send(json.dumps(order))

def monitor_profit_loss(ws, data):
    global active_contract_id

    if "profit" in data["contract_update"]:
        profit = float(data["contract_update"]["profit"])
        log(f"Current P/L: ${profit:.2f}")

        if profit <= STOP_LOSS:
            log(f"Stop Loss hit (${profit:.2f}). Closing trade...")
            close_trade(ws, active_contract_id)
            active_contract_id = None
        elif profit >= TAKE_PROFIT:
            log(f"Take Profit hit (${profit:.2f}). Closing trade...")
            close_trade(ws, active_contract_id)
            active_contract_id = None

def close_trade(ws, contract_id):
    close_msg = {"sell": contract_id, "price": 0}
    ws.send(json.dumps(close_msg))
    log(f"Closed contract {contract_id}")

# ===== BOT INITIALIZATION =====
def start_bot():
    global should_reconnect
    socket_url = f"wss://ws.derivws.com/websockets/v3?app_id={App_ID}"

    log("Starting Deriv Bot...")
    ws = websocket.WebSocketApp(
        socket_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

if __name__ == "__main__":
    try:
        start_bot()
    except KeyboardInterrupt:
        should_reconnect = False
        log("Bot stopped by user.")