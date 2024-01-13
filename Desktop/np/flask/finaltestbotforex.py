from flask import Flask
from flask_socketio import SocketIO, emit
import MetaTrader5 as mt5
from flask_cors import CORS
import threading
import time
import pandas as pd
from datetime import datetime
from collections import deque

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
CORS(app)

if not mt5.initialize(login=212678322, server="OctaFX-Demo", password="&Wem4EYY"):
    print("Initialize() failed, error code =", mt5.last_error())
    quit()
else:
    print("connected succesfully")


# constants
PIP_SIZE_EURUSD = 0.0015  # pip size
PIP_SIZE_GBPUSD = 0.0015  # pip size
PIP_SIZE_BTCUSD = 25  # pip size
TIMEFRAME = mt5.TIMEFRAME_M15

# histrical prices
historical_prices_eurusd = deque(maxlen=3600)
historical_prices_gbpusd = deque(maxlen=3600)
historical_prices_btcusd = deque(maxlen=3600)


def count_open_orders(symbol):
    # Fetch open orders for the given symbol
    orders = mt5.orders_get(symbol=symbol)
    if orders is None:
        return 0
    return len(orders)


def open_positions(symbol):
    positions = mt5.positions


def close_orders_in_profit(symbol):
    # Fetch open orders for the given symbol
    orders = mt5.orders_get(symbol=symbol)
    if orders is None or len(orders) == 0:
        return

    for order in orders:
        # Calculate the profit for each order (simplified example)
        profit = (
            order.profit
        )  # This is a simplified representation. Calculate profit based on your criteria.

        if profit > 0:  # If the order is in profit
            # Close the order
            close_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": order.volume,
                "type": mt5.ORDER_TYPE_SELL
                if order.type == mt5.ORDER_TYPE_BUY
                else mt5.ORDER_TYPE_BUY,
                "position": order.ticket,
                "magic": order.magic,
                "comment": "Closing profitable order",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            mt5.order_send(close_request)


def place_trade(symbol, price, trade_direction):
    close_orders_in_profit(symbol)
    sl = None
    tp = None
    lot = 0.01

    mt5_trade_type = None
    if trade_direction == "LONG":
        mt5_trade_type = mt5.ORDER_TYPE_BUY
        sl = price - 0.00708 * price  # Calculating stop loss for a long position
        tp = price + 0.3522 * price  # Calculating take profit for a long position
    elif trade_direction == "SHORT":
        mt5_trade_type = mt5.ORDER_TYPE_SELL
        sl = price + 0.00708 * price  # Calculating stop loss for a short position
        tp = price - 0.0181 * price  # Calculating take profit for a short position

    opened_and_running = mt5.positions_get(symbol=symbol)
    num_positions = len(opened_and_running) if opened_and_running is not None else 0
    print(f"Number of open positions for {symbol}: {num_positions}")

    if num_positions < 6:
        # Proceed with trade placement
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5_trade_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": 0,
            "comment": "POC Strategy Check",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = mt5.order_send(request)

        # Emit the stop loss and take profit values
        socketio.emit(
            f"price_{symbol.lower()}_tpsl",
            {"symbol": symbol, "current_price": price, "sl": sl, "tp": tp},
        )
    else:
        # Trade limit exceeded
        print(f"Trade limit exceeded for {symbol}. No new trade placed.")
        socketio.emit(f"price_{symbol.lower()}_positions", {"num_positions": num_positions, "message": "Trade limit exceeded"})

    return sl, tp



def stream_prices(symbol, pip_size, historical_prices):
    print(f"Streaming {symbol} started")  # Debug statement

    latest_high = None
    latest_low = None
    last_order_price = None
    is_trade_open = False
    trade_type = None
    prev_price = None
    pip_diff = None
    trade_direction = None
    pip_diff_short = None
    pip_diff_long = None

    while True:
        rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 1)
        if rates is not None:
            df = pd.DataFrame(rates)
            current_price = df["close"].iloc[-1]
            historical_prices.append(current_price)

        # Update high and low
        latest_high = (
            max(latest_high, current_price)
            if latest_high is not None
            else current_price
        )
        latest_low = (
            min(latest_low, current_price) if latest_low is not None else current_price
        )

        # Calculate pip difference
        if prev_price is not None:
            if symbol == "BTCUSD":
                pip_diff = round((current_price - prev_price), 2)
                if pip_diff >= 25:
                    trade_direction = "SHORT"
                elif pip_diff <= -25:
                    trade_direction = "LONG"
                else:
                    trade_direction = "NO Trade"
            if symbol in ["EURUSD", "GBPUSD"]:
                pip_diff_short = abs((latest_low - current_price) * 10000)
                pip_diff_long = abs((latest_high - current_price) * 10000)
                if pip_diff_short >= 15:  # Threshold for LONG
                    trade_direction = "SHORT"
                    place_trade(symbol, current_price, trade_direction)
                elif pip_diff_long >= 15:  # Threshold for SHORT
                    trade_direction = "LONG"
                    place_trade(symbol, current_price, trade_direction)
                else:
                    trade_direction = "NO Trade"

        # Calculate pip difference from last order price
        if last_order_price is not None:
            order_pip_diff = (current_price - last_order_price) / pip_size
            print(f"{symbol} Pip difference from last order: {order_pip_diff}")

        # Emit current price and other details
        socketio.emit(
            f"price_{symbol.lower()}",
            {
                "symbol": symbol,
                "current_price": current_price,
                "latest_high": latest_high,
                "latest_low": latest_low,
                "price_low_differece": abs(latest_low - current_price) * 10000,
                "price_high_differece": abs(latest_high - current_price) * 10000,
                # "pip_difference": pip_diff,
                "trade_direction": trade_direction,
            },
        )

        # Update previous price for next iteration
        prev_price = current_price
    else:
        print(f"Failed to get data for {symbol}")

    time.sleep(1)


@app.route("/")
def hello_forex():
    return "Hello Forex"


if __name__ == "__main__":
    # Creating threads for each symbol
    eurusd_thread = threading.Thread(
        target=stream_prices,
        args=("EURUSD", PIP_SIZE_EURUSD, historical_prices_eurusd),
        daemon=True,
    )
    btcusd_thread = threading.Thread(
        target=stream_prices,
        args=("BTCUSD", PIP_SIZE_BTCUSD, historical_prices_btcusd),
        daemon=True,
    )
    gbpusd_thread = threading.Thread(
        target=stream_prices,
        args=("GBPUSD", PIP_SIZE_GBPUSD, historical_prices_gbpusd),
        daemon=True,
    )

    # Start the threads
    try:
        eurusd_thread.start()
        btcusd_thread.start()
        gbpusd_thread.start()
    except Exception as e:
        print("Error starting threads:", str(e))

    # Run the Flask-SocketIO app
    socketio.run(app, debug=True)
