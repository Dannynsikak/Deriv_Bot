from datetime import datetime
import websocket
import json

API_TOKEN = "***********OFRp"
previous_tick = None

active_contract_id = None
entry_price = None
# Amounts in USD
STOP_LOSS = 1  # lose $1, close the trade
TAKE_PROFIT = 2  # gain $2, close the trade

def log(message):
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{time_now}]  {message}")
    with open("bot_logs.txt", "a") as file:
        file.write(f"[{time_now}]  {message}\n")

def on_open(ws):
    auth_request = {"authorize": API_TOKEN}
    ws.send(json.dumps(auth_request))
    log("Authentication request sent.")

def subscribe_to_ticks(ws, symbol="frxEURUSD"):
    ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))

def place_order(ws, action, amount=1, symbol="frxEURUSD"):
    global active_contract_id, entry_price

    trade_request = {
        "buy": 1 if action == "buy" else 0,
        "price": 0,
        "parameters": {
            "amount": amount,
            "basis": "stake",
            "contract_type": "CALL" if action == "buy" else "PUT",
            "currency": "USD",
            "duration": 5,
            "duration_unit": "t",
            "symbol": symbol
        }
    }
    ws.send(json.dumps(trade_request))

def close_trade(ws, contract_id):
    close_request = {
        "sell": contract_id,
        "price": 0
    }
    ws.send(json.dumps(close_request))
    log(f"Closing contract {contract_id}")

def monitor_profit_loss(ws, data):
    global active_contract_id

    if 'profit' in data.get('contract_update', {}):
        profit = data['contract_update']['profit']
        log(f"Current P/L: {profit} USD")

        if profit <= -STOP_LOSS:
            log(f"Stop Loss hit! Closing trade...")
            close_trade(ws, active_contract_id)
            active_contract_id = None
        elif profit >= TAKE_PROFIT:
            log(f"Take Profit hit! Closing trade...")
            close_trade(ws, active_contract_id)
            active_contract_id = None

def analyze_market(price):
    global previous_tick
    if previous_tick is None:
        previous_tick = price
        return None

    if price > previous_tick * 1.0002:
        return "buy"
    elif price < previous_tick * 0.9998:
        return "sell"
    previous_tick = price
    return None

def on_message(ws, message):
    global active_contract_id

    data = json.loads(message)

    # Check for authorization response
    if 'authorize' in data:
        if data['authorize']['valid']:
            log("Authorization successful. Subscribing to ticks...")
            subscribe_to_ticks(ws)
        else:
            log(f"Authorization failed: {data['error']['message']}")
            ws.close()
        return

    if 'tick' in data:
        price = float(data['tick']['quote'])
        log(f"Current price: {price}")
        signal = analyze_market(price)
        if signal and not active_contract_id:
            log(f"Signal detected: {signal.upper()} — Placing order...")
            place_order(ws, signal)

    if 'buy' in data:
        active_contract_id = data['buy']['contract_id']
        log(f"Trade opened! Contract ID: {active_contract_id}")

    if 'contract_update' in data and active_contract_id:
        monitor_profit_loss(ws, data)

def on_error(ws, error):
    log(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    log("Connection closed")

socket_url = "wss://ws.derivws.com/websockets/v3?app_id=72161"
ws = websocket.WebSocketApp(socket_url,
                             on_open=on_open,
                             on_message=on_message,
                             on_error=on_error,
                             on_close=on_close)


ws.run_forever(ping_interval=30, ping_timeout=10)