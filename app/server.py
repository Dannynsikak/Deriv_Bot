import os
from datetime import datetime
import websocket
import json
import pandas as pd
from ta.momentum import RSIIndicator
import pprint


API_TOKEN = os.getenv("DERIV_API_TOKEN", "**********Go2Hj")  
previous_tick = None
is_authorized = False

active_contract_id = None
entry_price = None
price_history = []
contract_stake_limits = {}
RSI_PERIOD = 14

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

def get_contracts_for(ws, symbol="frxEURUSD"):
    request = {
        "contracts_for": symbol,
        "currency": "USD",
    }
    ws.send(json.dumps(request))

def place_order(ws, action, amount, symbol="frxEURUSD"):
    global active_contract_id, entry_price

    trade_request = {
        "buy": 1,
        "price": 10.0,
        "parameters": {
            "amount": amount,
            "basis": "stake",
            "contract_type": "CALL" if action == "buy" else "PUT",
            "currency": "USD",
            "duration": 15,
            "duration_unit": "m",
            "symbol": symbol
        },
        "passthrough": {
            "action": action,
            "amount": amount,
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
    global price_history

    price_history.append(price)

    if len(price_history) < RSI_PERIOD:
        return None  # Not enough data yet

    df = pd.DataFrame(price_history, columns=["close"])
    rsi = RSIIndicator(close=df["close"]).rsi()
    current_rsi = rsi.iloc[-1]

    log(f"RSI: {current_rsi:.2f}")

    if current_rsi < 30:
        return "buy"
    elif current_rsi > 70:
        return "sell"
    return None

def on_message(ws, message):
    global active_contract_id, contract_stake_limits

    data = json.loads(message)

    if data.get('msg_type') == 'authorize':
        log("Authorization successful. Subscribing to ticks and fetching contract limits...")
        subscribe_to_ticks(ws)
        get_contracts_for(ws)
        return

    elif data.get('msg_type') == 'contracts_for':
        log("Received contracts_for data")
        pprint.pprint(data)

        # Extract min/max stake from the contract list
        for contract in data['contracts_for']['available']:
            if contract['contract_type'] in ['CALL', 'PUT']:
                log(f"{contract['contract_type']} duration: {contract.get('min_contract_duration')} - {contract.get('max_contract_duration')}, expiry: {contract.get('expiry_type')}")
                stake_info = contract.get('min_stake', 1.0), contract.get('max_stake', 1000.0)
                contract_stake_limits[contract['contract_type']] = stake_info
        log(f"Contract stake limits updated: {contract_stake_limits}")
        return

    elif data.get('error'):
        log(f"Error: {data['error']['message']}")
        ws.close()
        return

    elif 'tick' in data:
        price = float(data['tick']['quote'])
        log(f"Current price: {price}")
        signal = analyze_market(price)

        if signal and not active_contract_id:
            # Determine contract type
            contract_type = "CALL" if signal == "buy" else "PUT"
            min_stake, max_stake = contract_stake_limits.get(contract_type, (1.0, 1000.0))

            # Choose an amount within the allowed range (example logic)
            desired_amount = 1.0
            amount = max(min(desired_amount, max_stake), min_stake)

            log(f"Signal detected: {signal.upper()} — Placing order with amount: {amount}")
            place_order(ws, signal, amount)

    elif 'buy' in data:
        active_contract_id = data['buy']['contract_id']
        log(f"Trade opened! Contract ID: {active_contract_id}")

    elif 'contract_update' in data and active_contract_id:
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