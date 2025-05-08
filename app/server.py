import os
from datetime import datetime, timedelta
import websocket
import json
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# Configuration constants
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN")
if not DERIV_API_TOKEN:
    raise EnvironmentError("Please set the DERIV_API_TOKEN environment variable.")  

RSI_PERIOD = 14
EMA_PERIOD = 50
BB_PERIOD = 20
ATR_PERIOD = 14
CANDLE_INTERVAL = 15  # minutes
MAX_CANDLES = 1000
WARMUP_PERIODS = max(RSI_PERIOD, EMA_PERIOD, BB_PERIOD, ATR_PERIOD) + 10

# Global variables
active_contract_id = None
entry_price = None
entry_action = None
candles = []
current_candle = None
next_candle_time = None
contract_stake_limits = {}
ACCOUNT_BALANCE = None  
current_trade_atr = None
is_warmup_complete = False

def log(message):
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{time_now}]  {message}")
    with open("enhanced_bot_logs.txt", "a") as file:
        file.write(f"[{time_now}]  {message}\n")

def on_open(ws):
    auth_request = {"authorize": DERIV_API_TOKEN}
    ws.send(json.dumps(auth_request))
    log("Authentication request sent.")

def subscribe_to_ticks(ws, symbol="frxEURUSD"):
    ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
    log(f"Subscribed to {symbol} ticks")

def get_contracts_for(ws, symbol="frxEURUSD"):
    request = {
        "contracts_for": symbol,
        "currency": "USD",
    }
    ws.send(json.dumps(request))
    log("Requested contract details")

def subscribe_to_balance(ws):
    ws.send(json.dumps({"balance": 1, "subscribe": 1}))
    log("Subscribed to balance updates")

def calculate_position_size(current_atr, entry_price):
    if ACCOUNT_BALANCE is None:
        log("Error: Account balance not available")
        return 1.0  # Default minimum stake
    
    risk_per_trade = ACCOUNT_BALANCE * 0.01  # Risk 1% per trade
    position_size = risk_per_trade / (current_atr * 1.5)
    
    # Ensure position size is within contract limits
    contract_type = "CALL" if entry_action == "buy" else "PUT"
    min_stake, max_stake = contract_stake_limits.get(contract_type, (1.0, 1000.0))
    return max(min(position_size, max_stake), min_stake)

def place_order(ws, action, price, current_atr):
    global active_contract_id, entry_price, entry_action, current_trade_atr
    
    entry_action = action
    entry_price = price
    current_trade_atr = current_atr
    position_size = calculate_position_size(current_atr, price)
    
    trade_request = {
        "buy": 1,
        "price": price,
        "parameters": {
            "amount": round(position_size, 2),
            "basis": "stake",
            "contract_type": "CALL" if action == "buy" else "PUT",
            "currency": "USD",
            "duration": CANDLE_INTERVAL,
            "duration_unit": "m",
            "symbol": "frxEURUSD"
        },
        "passthrough": {
            "action": action,
            "atr": current_atr,
        }
    }
    ws.send(json.dumps(trade_request))
    log(f"Placing {action} order at {price:.5f} with size {position_size:.2f}")

def close_trade(ws, contract_id):
    close_request = {
        "sell": contract_id,
        "price": 0
    }
    ws.send(json.dumps(close_request))
    log(f"Closing contract {contract_id}")

def monitor_profit_loss(ws, data):
    global active_contract_id, entry_price, entry_action, current_trade_atr
    
    if 'profit' in data.get('contract_update', {}):
        profit = data['contract_update']['profit']
        current_price = float(data['contract_update']['current_spot'])
        atr = current_trade_atr

        if atr is None:
            log("Error: ATR is not available for current trade.")
            return
        
        # Dynamic exits
        if entry_action == "buy":
            stop_loss = entry_price - (atr * 1.5)
            take_profit = entry_price + (atr * 3)
            trailing_stop = current_price - (atr * 0.5)
        else:  # sell
            stop_loss = entry_price + (atr * 1.5)
            take_profit = entry_price - (atr * 3)
            trailing_stop = current_price + (atr * 0.5)
        
        log(f"P/L: ${profit:.2f} | Price: {current_price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f}")
        
        # Exit conditions
        if (entry_action == "buy" and (current_price <= stop_loss or current_price >= take_profit)) or \
           (entry_action == "sell" and (current_price >= stop_loss or current_price <= take_profit)):
            log(f"Exit condition met. Closing trade.")
            close_trade(ws, active_contract_id)
            active_contract_id = None
        elif ((entry_action == "buy" and current_price > entry_price + atr) or \
             (entry_action == "sell" and current_price < entry_price - atr)) and \
             ((entry_action == "buy" and current_price < trailing_stop) or \
              (entry_action == "sell" and current_price > trailing_stop)):
            log(f"Trailing stop triggered at {trailing_stop:.5f}")
            close_trade(ws, active_contract_id)
            active_contract_id = None

def process_tick(price):
    global current_candle, candles, next_candle_time, is_warmup_complete
    
    now = datetime.now()
    
    # Initialize first candle
    if current_candle is None:
        current_candle = {
            'timestamp': now.replace(second=0, microsecond=0),
            'open': price,
            'high': price,
            'low': price,
            'close': price
        }
        next_candle_time = current_candle['timestamp'] + timedelta(minutes=CANDLE_INTERVAL)
        return False
    
    # Update current candle
    current_candle['high'] = max(current_candle['high'], price)
    current_candle['low'] = min(current_candle['low'], price)
    current_candle['close'] = price
    
    # Check if candle should close
    if now >= next_candle_time:
        candles.append(current_candle)
        if len(candles) > MAX_CANDLES:
            candles.pop(0)
        
        log(f"New candle: O:{current_candle['open']:.5f} H:{current_candle['high']:.5f} "
            f"L:{current_candle['low']:.5f} C:{current_candle['close']:.5f}")
        
        # Start new candle
        current_candle = {
            'timestamp': next_candle_time,
            'open': price,
            'high': price,
            'low': price,
            'close': price
        }
        next_candle_time = current_candle['timestamp'] + timedelta(minutes=CANDLE_INTERVAL)
        
        # Check warmup status
        if not is_warmup_complete and len(candles) >= WARMUP_PERIODS:
            is_warmup_complete = True
            log(f"Warmup complete! Collected {WARMUP_PERIODS} candle periods")
        
        return True  # Signal that new candle is ready
    
    return False

def analyze_market(price):
    global candles, is_warmup_complete
    
    # Process the tick and check if new candle formed
    new_candle = process_tick(price)
    
    # Only analyze at candle close
    if not new_candle or not is_warmup_complete:
        return None, None
    
    # Create DataFrame from candles
    df = pd.DataFrame(candles)
    
    try:
        # Calculate indicators
        df['rsi'] = RSIIndicator(close=df["close"], window=RSI_PERIOD).rsi()
        df['ema_50'] = EMAIndicator(close=df["close"], window=EMA_PERIOD).ema_indicator()
        
        bb = BollingerBands(close=df["close"], window=BB_PERIOD, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_middle'] = bb.bollinger_mavg()
        df['bb_lower'] = bb.bollinger_lband()
        
        atr = AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=ATR_PERIOD)
        current_atr = atr.average_true_range().iloc[-1]
        
        # Current values
        current_rsi = df['rsi'].iloc[-1]
        current_ema = df['ema_50'].iloc[-1]
        current_bb_upper = df['bb_upper'].iloc[-1]
        current_bb_lower = df['bb_lower'].iloc[-1]
        current_close = df['close'].iloc[-1]
        
        # Validate indicator values
        if not all(pd.notna([current_rsi, current_ema, current_bb_upper, current_bb_lower, current_atr])):
            log("Warning: Some indicator values are NaN, skipping analysis")
            return None, None
            
        if not all(np.isfinite([current_rsi, current_ema, current_bb_upper, current_bb_lower, current_atr])):
            log("Warning: Some indicator values are infinite, skipping analysis")
            return None, None
        
        # Determine trend
        current_trend = "bullish" if current_close > current_ema else "bearish"
        
        # Generate signals
        buy_signal = (
            (current_rsi < 35) and
            (current_close < current_bb_lower) and
            (current_trend == "bearish") and
            (current_atr > 0)
        )
        
        sell_signal = (
            (current_rsi > 65) and
            (current_close > current_bb_upper) and
            (current_trend == "bullish") and
            (current_atr > 0)
        )
        
        log(f"Analysis: Close={current_close:.5f} | RSI={current_rsi:.2f} | EMA={current_ema:.5f} | ATR={current_atr:.5f}")
        log(f"Trend: {current_trend} | BB: {current_bb_lower:.5f}-{current_bb_upper:.5f}")
        
        if buy_signal:
            return ("buy", current_atr)
        elif sell_signal:
            return ("sell", current_atr)
        
        return None, current_atr
    
    except Exception as e:
        log(f"Error in analysis: {str(e)}")
        return None, None

def on_message(ws, message):
    global active_contract_id, contract_stake_limits, ACCOUNT_BALANCE
    
    data = json.loads(message)
    
    if data.get('msg_type') == 'authorize':
        if 'authorize' in data:
            ACCOUNT_BALANCE = float(data['authorize']['balance'])
            log(f"Authorization successful. Balance: ${ACCOUNT_BALANCE:.2f}")
            subscribe_to_ticks(ws)
            get_contracts_for(ws)
            subscribe_to_balance(ws)
        return
    
    elif data.get('msg_type') == 'balance':
        if 'balance' in data:
            new_balance = float(data['balance']['balance'])
            if ACCOUNT_BALANCE is None or abs(new_balance - ACCOUNT_BALANCE) > 0.01:
                ACCOUNT_BALANCE = new_balance
                log(f"Account balance updated: ${ACCOUNT_BALANCE:.2f}")
        return
    
    elif data.get('msg_type') == 'contracts_for':
        log("Received contracts_for data")
        for contract in data['contracts_for']['available']:
            if contract['contract_type'] in ['CALL', 'PUT']:
                stake_info = (contract.get('min_stake', 1.0), contract.get('max_stake', 1000.0))
                contract_stake_limits[contract['contract_type']] = stake_info
        log(f"Contract limits: {contract_stake_limits}")
        return
    
    elif data.get('error'):
        log(f"Error: {data['error']['message']}")
        return
    
    elif 'tick' in data:
        price = float(data['tick']['quote'])
        signal, current_atr = analyze_market(price)

        if not is_warmup_complete:
            return
        
        if signal and current_atr is not None and current_atr > 0 and not active_contract_id and ACCOUNT_BALANCE is not None:
            place_order(ws, signal, current_candle['close'], current_atr)
    
    elif 'buy' in data:
        if 'error' in data['buy']:
            log(f"Order failed: {data['buy']['error']['message']}")
        else:
            active_contract_id = data['buy']['contract_id']
            log(f"Trade opened! Contract ID: {active_contract_id}")
    
    elif 'contract_update' in data and active_contract_id:
        monitor_profit_loss(ws, data)

def on_error(ws, error):
    log(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    log(f"Connection closed. Code: {close_status_code}, Message: {close_msg}")

if __name__ == "__main__":
    socket_url = "wss://ws.derivws.com/websockets/v3?app_id=72161"
    ws = websocket.WebSocketApp(socket_url,
                              on_open=on_open,
                              on_message=on_message,
                              on_error=on_error,
                              on_close=on_close)
    
    log("Starting Enhanced Deriv Trading Bot with 15-Minute Candle Analysis")
    ws.run_forever(ping_interval=30, ping_timeout=10)