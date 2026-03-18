import logging
import threading
import requests
import time
import typing
import collections
from urllib.parse import urlencode
import hmac
import hashlib
import json
from math import log, floor
from binance import ThreadedWebsocketManager
from strategies import Strategy, TechnicalStrategy, BreakoutStrategy, IchimokuStrategy
from models.models import *
logger = logging.getLogger()

class BinanceClient:
    WS_URL = "wss://stream.binance.com:9443/ws/"

    # Combined mapping for all stream types
    STREAM_SUFFIXES = {
        "aggtrade": "@aggTrade",
        "bookticker": "@bookTicker",
        "kline": "@kline"
    }

    _ws_startup_lock = False



    def __init__(self, public_key: str, secret_key: str, testnet: bool, futures: bool, pnl_update_callback=None, on_trade_update=None, root=None):

        self.root = root

        self.ws_subscriptions: typing.Dict = {
            "bookticker": [],
            "aggtrade": [],
            "kline": []
        }

        self.pnl_update_callback = pnl_update_callback
        self.futures = futures
        self.on_trade_update = on_trade_update

        if self.futures:
            self.platform = "binance_futures"
            if testnet:
                self._base_url = "https://demo-fapi.binance.com"
                # FUTURES TESTNET: Base + /stream
                self._wss_url = "wss://fstream.binancefuture.com/stream"
            else:
                self._base_url = "https://fapi.binance.com"
                # FUTURES LIVE: Base + /stream
                self._wss_url = "wss://fstream.binance.com/stream"
        else:
            self.platform = "binance_spot"
            if testnet:
                self._base_url = "https://api.binance.com"
                # SPOT TESTNET: stream.subdomain + /ws/stream (The standard Spot Testnet combined stream)
                self._wss_url = "wss://stream.binance.com:9443/stream"
            else:
                self._base_url = "https://api.binance.com"
                # SPOT LIVE: Base + :9443 + /stream
                self._wss_url = "wss://stream.binance.com:9443/stream"

        self._public_key = public_key
        self._secret_key = secret_key
        self._headers = {'X-MBX-APIKEY': self._public_key}

        if not self.futures:
            time.sleep(0.5)

        self.contracts = {}
        self.balances = {}
        self.prices = dict()
        self.strategies: typing.Dict[int, typing.Union[TechnicalStrategy, BreakoutStrategy]] = dict()
        self.strategies_lock = threading.Lock()
        self.logs = []

        # Initialize the object but DO NOT call .start() here
        self._twm = ThreadedWebsocketManager(api_key=self._public_key,
                                             api_secret=self._secret_key,
                                             testnet=testnet)

        if self.futures:
            self._twm.is_futures = True

        self.ws_connected = False
        self.last_update_time = time.time()
        self.is_ready = False

        #if not self.contracts:
         #   logger.error("CRITICAL: Failed to load contracts during initialization.")

        if self.futures:
            logger.info("Binance Futures Client successfully initialized (WS deferred)")

        self.testnet = testnet
        self.active_trades: typing.List[Trade] = []
        self.pnl_update_queue = None
        self._active_subscriptions = set()
        self.strategies: typing.Dict[str, Strategy] = {}

    def connect(self):
        try:
            self.get_contracts()

            if self.contracts and len(self.contracts) > 0:
                self.is_ready = True
                print(f"DEBUG: Successfully loaded {len(self.contracts)} symbols.")
                return True
            else:
                print("DEBUG: Binance returned empty symbols list.")
                return False
        except Exception as e:
            print(f"DEBUG: Connection failed during connect(): {e}")
            return False

    def _add_log(self, msg: str):

        logger.info("%s", msg)
        self.logs.append({"log": msg, "displayed": False})

    def _generate_signature(self, data: typing.Dict) -> str:
        query_string = urlencode(data)
        return hmac.new(
            self._secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _make_request(self, method: str, endpoint: str, data: typing.Dict):

        url = self._base_url + endpoint
        # Standard Binance Header
        headers = {'X-MBX-APIKEY': self._public_key}
        # Timeout: (Connect timeout, Read timeout)
        timeout = (10, 20)
        response = None

        try:
            if method == "GET":
                response = requests.get(url, params=data, headers=headers, timeout=timeout)

            elif method == "POST":
                response = requests.post(url, params=data, headers=headers, timeout=timeout)

            elif method == "DELETE":
                response = requests.delete(url, params=data, headers=headers, timeout=timeout)

            else:
                logger.error(f"Error while making {method} request to {endpoint}: "
                         f"{response.json()} (Status {response.status_code})")
                return None

        except requests.exceptions.Timeout as er:
            logger.error(f"Connection error while making {method} request to {endpoint}: {er}")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"Binance Connection Error: Could not reach {url}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during {method} {endpoint}: {e}")
            return None

        # Handle Response
        if response is not None:
            if response.status_code == 200:
                return response.json()
            else:
                # Log the specific error from Binance
                try:
                    err_data = response.json()
                    logger.error(f"Binance API Error {response.status_code} ({endpoint}): {err_data}")
                except Exception:
                    logger.error(f"Binance API Error {response.status_code} ({endpoint}): {response.text}")
                return None  # This returns None to the caller

        return None

    def get_contracts(self) -> typing.Dict[str, Contract]:

        # 1. Determine correct endpoint based on Futures vs Spot
        endpoint = "/fapi/v1/exchangeInfo" if self.futures else "/api/v3/exchangeInfo"

        try:
            exchange_info = self._make_request("GET", endpoint, dict())
        except Exception as e:
            logger.error(f"Error fetching exchange info from {endpoint}: {e}")
            return {}

        # Initialize a temporary dict to avoid thread-safety issues with the GUI
        temp_contracts = dict()

        if exchange_info is not None and 'symbols' in exchange_info:
            for contract_data in exchange_info['symbols']:
                # 2. Filter for active trading pairs only
                if contract_data.get('status') != 'TRADING':
                    continue

                # 3. Handle Symbol and Key Alignment
                raw_symbol = contract_data['symbol'].upper()
                contract_key = raw_symbol

                # 4. Create the Contract Object
                temp_contracts[contract_key] = Contract(contract_data, self.platform)

            logging.info(f"Binance {'Futures' if self.futures else 'Spot'} loaded {len(temp_contracts)} symbols.")

            # 5. Finalize the dictionary update
            self.contracts = temp_contracts
            return self.contracts
        else:
            logging.warning("Exchange info received was empty or malformed.")
            return {}

    def get_historical_candles(self, contract: Contract, interval: str) -> typing.List[Candle]:

        data = dict()
        data['symbol'] = contract.symbol
        data['interval'] = interval
        data['limit'] = 1000

        endpoint = "/fapi/v1/klines" if self.futures else "/api/v3/klines"

        # Add a log so we know the request started
        logger.info(f"Fetching {data['limit']} candles for {contract.symbol}...")

        try:
            raw_candles = self._make_request("GET", endpoint, data)
        except Exception as e:
            logger.error(f"Error connecting to {endpoint}: {e}")
            return []

        candles = []

        if raw_candles is not None:
            for c in raw_candles:
                # We pass the raw list 'c' to our Candle constructor
                candles.append(Candle(c, interval, self.platform))

            logger.info(f"Successfully loaded {len(candles)} candles for {contract.symbol}")
        else:
            logger.error(f"Failed to fetch candles for {contract.symbol} - Binance returned None")

        return candles

    def get_bid_ask(self, contract: Contract) -> typing.Dict[str, float]:


        data = dict()
        data['symbol'] = contract.symbol

        if self.futures:
            ob_data = self._make_request("GET", "/fapi/v1/ticker/bookTicker", data)
        else:
            ob_data = self._make_request("GET", "/api/v3/ticker/bookTicker", data)

        if ob_data is not None:
            if contract.symbol not in self.prices:  # Add the symbol to the dictionary if needed
                self.prices[contract.symbol] = {'bid': float(ob_data['bidPrice']), 'ask': float(ob_data['askPrice'])}
            else:
                self.prices[contract.symbol]['bid'] = float(ob_data['bidPrice'])
                self.prices[contract.symbol]['ask'] = float(ob_data['askPrice'])

            return self.prices[contract.symbol]

    def get_balances(self) -> typing.Dict[str, Balance]:

        data = {
            'timestamp': int(time.time() * 1000),
            'recvWindow': 60000
        }
        # Generate signature based on the data dict
        data['signature'] = self._generate_signature(data)

        endpoint = "/fapi/v2/account" if self.futures else "/api/v3/account"
        account_data = self._make_request("GET", endpoint, data)

        balances = dict()

        # 4. Parse the response based on the platform
        if account_data is not None:
            try:
                # Futures uses 'assets', Spot uses 'balances'
                key = 'assets' if self.futures else 'balances'
                if key in account_data:
                    for a in account_data[key]:
                        balances[a['asset']] = Balance(a, self.platform)
            except Exception as e:
                logger.error("Error parsing Binance balance data: %s", e)

        return balances

    def place_order(self, contract: Contract, order_type: str, quantity: float, side: str, price=None, tif=None) -> OrderStatus:

        # 1. Calculate the valid quantity using your new helper
        rounded_qty = self.round_step_size(quantity, contract.lot_size)

        if rounded_qty <= 0:
            logger.error(f"Order rejected: Rounded quantity is 0 for {contract.symbol}")
            return None

        data = dict()
        data['symbol'] = contract.symbol
        data['side'] = side.upper()

        # FIX: Ensure quantity is rounded to lot size to avoid Binance errors
        data['quantity'] = rounded_qty
        data['type'] = order_type.upper()

        if price is not None:
            # Ensure tick_size and price are floats
            tick_size = float(contract.tick_size)
            f_price = float(price)

            rounded_price = round(round(f_price / tick_size) * tick_size, 8)
            data['price'] = '%.*f' % (contract.price_decimals, rounded_price)

        if tif is not None:
            data['timeInForce'] = tif

        data['timestamp'] = int(time.time() * 1000)
        data['recvWindow'] = 60000
        endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"
        data['signature'] = self._generate_signature(data)

        logger.info("DEBUG_ORDER_DATA: Placing order with payload: %s", data)

        endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"
        response = self._make_request("POST", endpoint, data)

        if response is not None:
            if 'orderId' in response:

                # Spot-specific execution price logic
                if not self.futures:
                    if response.get('status') == "FILLED":
                        response['avgPrice'] = self._get_execution_price(contract, response['orderId'])
                    else:
                        response['avgPrice'] = 0

                logger.info(f"ORDER SUCCESS: {response.get('symbol')} ID: {response.get('orderId')}")

                # 1. Create the Model object
                order_status = OrderStatus(response, self.platform)

                # If it's a futures trade, we update the liquidation price immediately
                if self.futures:
                    # Give the exchange a millisecond to update its calculation
                    time.sleep(0.1)
                    liq_price = self.get_liquidation_price(contract.symbol)


                # 2. TRIGGER THE CALLBACK (This updates the GUI table)
                if hasattr(self, 'on_trade_update') and self.on_trade_update is not None:
                    self.on_trade_update(order_status)

                return order_status
            else:
                logger.error("Binance API returned success code but no Order ID: %s", response)
        # --- CRITICAL SAFETY WRAPPER END ---

        return None

    def cancel_order(self, contract: Contract, order_id: int) -> OrderStatus:

        data = dict()
        data['orderId'] = order_id
        data['symbol'] = contract.symbol

        data['timestamp'] = int(time.time() * 1000)
        data['recvWindow'] = 60000

        endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"
        data['signature'] = self._generate_signature(data)

        if self.futures:
            order_status = self._make_request("DELETE", "/fapi/v1/order", data)
        else:
            order_status = self._make_request("DELETE", "/api/v3/order", data)

        if order_status is not None:
            if not self.futures:
                # Get the average execution price based on the recent trades
                order_status['avgPrice'] = self._get_execution_price(contract, order_id)
            order_status = OrderStatus(order_status, self.platform)

        return order_status

    def _get_execution_price(self, contract: Contract, order_id: int) -> float:
        data = {
            'timestamp': int(time.time() * 1000),
            'symbol': contract.symbol,
            'signature': None  # placeholder
        }

        # Select correct endpoint for Futures vs Spot
        endpoint = "/fapi/v1/userTrades" if self.futures else "/api/v3/myTrades"

        data['signature'] = self._generate_signature(data)
        trades = self._make_request("GET", endpoint, data)


        #data['recvWindow'] = 60000

        avg_price = 0
        if trades:
            relevant_trades = [t for t in trades if int(t.get('orderId')) == order_id]
            if not relevant_trades:
                return 0.0

            total_qty = sum(float(t['qty']) for t in relevant_trades)
            for t in relevant_trades:
                fill_pct = float(t['qty']) / total_qty
                avg_price += (float(t['price']) * fill_pct)

        return round(round(avg_price / contract.tick_size) * contract.tick_size, 8)

    def get_order_status(self, contract: Contract, order_id: int) -> OrderStatus:

        data = {
            'timestamp': int(time.time() * 1000),
            'symbol': contract.symbol,
            'orderId': order_id,
            'recvWindow': 60000
        }
        # FIX: Only pass the data dictionary
        data['signature'] = self._generate_signature(data)

        endpoint = "/fapi/v1/order" if self.futures else "/api/v3/order"
        raw_response = self._make_request("GET", endpoint, data)

        if raw_response:
            return OrderStatus(raw_response, self.platform)
        return None

    def _on_message(self, msg: typing.Dict):
        if not msg: return

        # FIX: Multiplexed streams wrap the event in a 'data' key
        data = msg.get('data', msg)

        if not isinstance(data, dict): return

        event_type = data.get("e")
        # If it's a 'result' message (subscription confirmation), log it and return
        if not event_type:
            if "result" in data:
                logger.info(f"Binance: Subscription confirmed: {data}")
            return

        # 1. HANDLE KLINE (Candle Closing)
        if event_type == "kline":
            if data['k'].get('x'):  # Candle closed
                symbol = data['s'].upper()
                with self.strategies_lock:
                    for strategy in self.strategies.values():
                        if strategy.contract.symbol == symbol:
                            self.root.after(0, strategy.check_trade, "new_candle")
            return

        # 2. HANDLE TRADES (Price Updates)
        if event_type in ["aggTrade", "trade"]:
            symbol = data.get('s', "").upper()
            price = float(data.get('p', 0))
            self.prices[symbol] = {'bid': price, 'ask': price}
            self.last_update_time = time.time()

            with self.strategies_lock:
                for strategy in self.strategies.values():
                    if strategy.contract.symbol == symbol:
                        # If we have a position, check every tick for SL/TP
                        if strategy.ongoing_position:
                            self.root.after(0, strategy.check_trade, "trade")
                        # Otherwise, only check every ~30s for a Heartbeat
                        elif time.time() - strategy._last_hb_time > 28:
                            self.root.after(0, strategy.check_trade, "heartbeat_check")

    def subscribe_channel(self, contracts: typing.List[Contract], channel_type: str, reconnection=False, interval: str = None):

        if self._twm is None:
            return

        streams_to_subscribe = []

        for contract in contracts:
            sym = contract.symbol.lower()

            # 1. Formatting Logic
            if channel_type == "kline" and interval:
                # Format: btcusdt@kline_1m
                stream_name = f"{sym}@kline_{interval}"
            else:
                suffix = self.STREAM_SUFFIXES.get(channel_type)
                if not suffix:
                    continue
                # Format: btcusdt@aggTrade
                stream_name = f"{sym}{suffix}"

            if stream_name not in self._active_subscriptions:
                streams_to_subscribe.append(stream_name)

        if streams_to_subscribe:
            if not self.ws_connected:
                self.start_ws_thread()

                # RETRY LOOP START
                for attempt in range(1, 6):
                    try:
                        if self._twm:
                            # Attempt the subscription directly
                            if self.futures:
                                self._twm.start_futures_multiplex_socket(callback=self._on_message,
                                                                         streams=streams_to_subscribe)
                            else:
                                self._twm.start_multiplex_socket(callback=self._on_message,
                                                                 streams=streams_to_subscribe)

                            # If we reach here, it was successful!
                            logger.info(f"Binance: New subscriptions successful: {streams_to_subscribe}")
                            for s in streams_to_subscribe:
                                self._active_subscriptions.add(s)

                            return  # Exit the retry loop on success

                    except Exception as e:
                        # If it's a "Socket Manager not ready" error, we wait and retry
                        logger.warning(f"Attempt {attempt}: Multiplex failed. Error: {e}. Waiting 2s...")

                    time.sleep(2)

    def get_trade_size(self, contract: Contract, price: float, balance_pct: float):
        logger.info("Getting Binance trade size...")

        # 1. Fetch the latest balances
        balances = self.get_balances()

        if balances is not None:
            if contract.quote_asset in balances:
                if self.futures:
                    # For Futures, we use the total wallet balance
                    balance = balances[contract.quote_asset].wallet_balance
                else:
                    # For Spot, we use the available free balance
                    balance = balances[contract.quote_asset].free
            else:
                logger.warning("WARNING: Quote asset balance (%s) not found in client balances.", contract.quote_asset)
                return None
        else:
            return None

        # 2. Leverage and Risk Calculation
        # Note: If trading Futures, ensure balance_pct accounts for your leverage.
        # Calculation: (Balance * % of Balance) / Price
        raw_trade_size = (balance * (balance_pct / 100)) / price

        # 3. Rounding to Lot Size (Step Size)
        # This uses your existing round_quantity which references contract.lot_size
        final_trade_size = self.round_quantity(contract, raw_trade_size)

        # 4. Minimum Notional / Minimum Quantity Check
        # Binance will reject orders below a certain USD value (usually $5 or $10)
        if final_trade_size <= 0.0:
            logger.warning(
                "WARNING: Calculated raw trade size (%.8f) resulted in zero after rounding for %s. "
                "The position size might be smaller than the minimum lot size.",
                raw_trade_size, contract.symbol)
            return None

        logger.info("DEBUG_TRADE_SIZE: %s Balance = %s, Calculated Quantity = %.8f",
                    contract.quote_asset, balance, final_trade_size)

        return final_trade_size

    def start_ws_thread(self):
        """
        Starts the Binance WebSocket Manager and blocks the worker thread
        until the internal socket engine is confirmed ready.
        """
        if self._twm is None:
            logger.error("Binance WS: ThreadedWebsocketManager is None. Cannot start.")
            return

        try:
            # 1. Start the TWM event loop if it's not already running
            if not getattr(self._twm, '_running', False):
                logger.info("Starting Binance TWM loop...")
                self._twm.start()

                # 2. THE ENGINE CHECK: Poll until the internal engine exists
                # We give it 10 seconds (20 attempts * 0.5s)
                for i in range(20):
                    manager = getattr(self._twm, '_socket_manager', None)
                    if manager is not None:
                        # Final check: is the background thread actually alive?
                        if hasattr(manager, 'is_alive') and manager.is_alive():
                            logger.info(f"Binance WS: Engine confirmed READY after {i * 0.5}s.")
                            break
                    time.sleep(0.5)
                else:
                    logger.error("CRITICAL: TWM started but _socket_manager failed to initialize.")
                    return

                # 3. Start the User Data Stream (Account updates, PNL, Balances)
                # This is essential for your bot to know when a trade is filled
                try:
                    if self.futures:
                        self.listen_key = self._twm.start_futures_user_data_socket(callback=self._on_message)
                        logger.info("Binance Futures: User Data Stream active.")
                    else:
                        self.listen_key = self._twm.start_user_data_socket(callback=self._on_message)
                        logger.info("Binance Spot: User Data Stream active.")
                except Exception as user_data_err:
                    logger.error(f"Failed to start User Data Stream: {user_data_err}")

            # 4. Final state update
            self.ws_connected = True
            keep_alive_thread = threading.Thread(target=self._keep_alive_listen_key, daemon=True)
            keep_alive_thread.start()
            logger.info("Binance WS: Hard-start successful and connected.")

        except Exception as e:
            logger.error(f"WS Startup Error: {e}")
            self.ws_connected = False

    def start_symbol_ticker_socket(self, callback, symbol):
        # 1. Log the subscription request using the correct global logger
        logger.info("Binance: preparing to subscribe to %s@bookticker", symbol.lower())

        # 2. Add the symbol to the existing bookticker subscription list
        if symbol not in self.ws_subscriptions["bookticker"]:
            self.ws_subscriptions["bookticker"].append(symbol)

        # 3. Subscription method
        self.subscribe_channel(contracts=[self.contracts[symbol]], channel="bookticker")


    def round_quantity(self, contract: Contract, quantity: float) -> float:

        # 1. Calculate how many full lots we can trade
        num_lots = int(quantity / contract.lot_size)

        # 2. Check if we can buy at least one lot
        if num_lots <= 0:
            logger.warning(
                "WARNING: Quantity %.8f is less than the minimum lot size %.8f for %s.",
                quantity, contract.lot_size, contract.symbol)
            return 0.0

        # 3. Calculate the final trade size and round it to 8 decimal places for safety/precision
        final_trade_size = round(num_lots * contract.lot_size, 8)

        return final_trade_size

    def close_connections(self):
        if self._twm:
            self._twm.stop()
            logger.info("Binance TWM stopped for %s", self.platform)

    def reconnect_ws(self):
        """Production recovery: Restores data flow for this specific client."""
        logger.warning(f"Restoring {self.platform} WebSocket connection...")
        try:
            self.start_ws_thread()

            # --- CRITICAL FIX: THREAD-SAFE RE-SUBSCRIPTION ---
            with self.strategies_lock: # Prevent UI from deleting while we re-subscribe
                for b_index, strat in self.strategies.items():
                    # Re-subscribe to all necessary channels
                    self.subscribe_channel([strat.contract], "aggtrade")
                    self.subscribe_channel([strat.contract], "bookticker")
                    self.subscribe_channel([strat.contract], "kline", interval=strat.tf)

            logger.info(f"{self.platform} recovery sequence complete.")
        except Exception as e:
            logger.error(f"Critical failure during {self.platform} reconnect: {e}")

    def check_connection(self):
        # 1. Immediate Recovery: If disconnected but strategies are running
        if not self.ws_connected:
            if len(self.strategies) > 0:
                logger.warning(f"WS disconnected on {self.platform} with active strategies. Reconnecting...")
                self.start_ws_thread()
            return

        # 2. Heartbeat Check: Verify if data is actually flowing
        time_since_last_update = time.time() - self.last_update_time

        if time_since_last_update > 60:
            logger.warning(f"Heartbeat lost on {self.platform} ({int(time_since_last_update)}s since last update).")
            self.reconnect_ws()
            return  # Exit here so we don't try to sync on a dead connection

        # 3. Auto-Sync: Ensure all active strategies have a price stream
        if len(self.strategies) > 0:
            missing_subscriptions = []
            for strategy in self.strategies.values():
                if strategy.contract.symbol not in self.prices:
                    missing_subscriptions.append(strategy.contract)

            if missing_subscriptions:
                logger.info(
                    f"Sync: Subscribing to {len(missing_subscriptions)} missing price streams for {self.platform}.")
                # bookticker is best for PnL and strategy execution
                self.subscribe_channel(missing_subscriptions, "bookticker")

    def get_liquidation_price(self, symbol: str) -> float:
        if not self.futures:
            return 0.0

        data = {
            'symbol': symbol,
            'timestamp': int(time.time() * 1000)
        }
        data['signature'] = self._generate_signature(data) # Removed extra args

        endpoint = "/fapi/v2/positionRisk"
        positions = self._make_request("GET", endpoint, data)

        if positions:
            for pos in positions:
                if pos['symbol'] == symbol:
                    return float(pos.get('liquidationPrice', 0.0))
        return 0.0

    def get_open_positions(self) -> typing.List[typing.Dict]:
        if not self.futures:
            return []

        data = {
            'timestamp': int(time.time() * 1000),
            'recvWindow': 10000
        }
        # Signature only requires the data dictionary
        data['signature'] = self._generate_signature(data)
        endpoint = "/fapi/v2/positionRisk"

        try:
            raw_positions = self._make_request("GET", endpoint, data)
            open_positions = []

            if raw_positions is not None:
                for pos in raw_positions:
                    # Filter for symbols where we actually have a position
                    size = float(pos.get('positionAmt', 0.0))

                    if size != 0:
                        open_positions.append({
                            'symbol': pos['symbol'],
                            'side': 'long' if size > 0 else 'short',
                            'entry_price': float(pos['entryPrice']),
                            'size': abs(size),
                            'pnl': float(pos['unRealizedProfit']),
                            'liq_price': float(pos['liquidationPrice'])
                        })

                # Useful log for debugging sync results
                if len(open_positions) > 0:
                    logger.info(f"Sync: Found {len(open_positions)} active positions on Binance.")

            return open_positions

        except Exception as e:
            logger.error(f"Failed to fetch open positions from {endpoint}: {e}")
            return []

    def round_step_size(self, quantity, step_size):
        try:
            # Convert to float just in case
            step_size = float(step_size)

            # SAFETY: If step_size is 0 or invalid, we can't do math on it.
            # We default to a standard precision (like 3 or 5) or return the quantity as is.
            if step_size <= 0:
                logger.warning(f"Invalid step_size {step_size}. Returning quantity without rounding.")
                return quantity

            precision = int(round(-log(step_size, 10), 0))
            return floor(quantity * 10 ** precision) / 10 ** precision
        except Exception as e:
            logger.error(f"Error in round_step_size: {e}")
            return quantity

    def _keep_alive_listen_key(self):
        """Pings the listenKey every 30 minutes to keep the User Data Stream alive."""
        while True:
            time.sleep(1800) # Wait 30 minutes first
            if not self.ws_connected:
                continue

            endpoint = "/fapi/v1/listenKey" if self.futures else "/api/v3/listenKey"
            # We don't need to do anything with the result, just ensure the call happens
            self._make_request("PUT", endpoint, dict())
            logger.info(f"Binance {self.platform} listenKey extended.")

    def start_strategy(self, params: dict, b_index: int):
        # 1. Parameter Extraction & Validation
        symbol = params.get('contract') or params.get('contract_str')
        tf = params.get('timeframe')
        strat_type = params.get('strategy_type')

        if not all([symbol, tf, strat_type]):
            logger.error(f"BinanceClient: Missing required parameters. Symbol: {symbol}, TF: {tf}, Type: {strat_type}")
            return

        # 2. Contract Lookup
        contract = self.contracts.get(symbol)
        if not contract:
            logger.error(f"BinanceClient: Contract {symbol} not found in exchange info.")
            return

        # 3. Create Unique Strategy ID
        strat_id = f"{symbol}_{tf}_{strat_type}"

        # 4. Data Guard: Fetch Historical Data (OUTSIDE THE LOCK)
        # This prevents the GUI from hanging during the 1000-candle download.
        logger.info(f"BinanceClient: Fetching 1000 candles for {strat_id}...")
        try:
            # We fetch these manually so we can pass them to the strategy on birth
            historical_candles = self.get_historical_candles(contract, tf)
            if not historical_candles or len(historical_candles) < 100:
                logger.error(f"BinanceClient: Insufficient data for {strat_id}. Strategy aborted.")
                return
        except Exception as e:
            logger.error(f"BinanceClient: Failed to fetch candles for {strat_id}: {e}")
            return

        # 5. Add row index to extra_params for UI callbacks
        if 'extra_params' not in params:
            params['extra_params'] = {}
        params['extra_params']['row_index'] = b_index

        # 6. Instantiate the Strategy Class
        # ui_callback uses the queue.put method from the root to remain thread-safe
        try:
            if strat_type == "Ichimoku":
                new_strat = IchimokuStrategy(self, contract, self.platform, tf,
                                             params['balance_pct'], params['take_profit'],
                                             params['stop_loss'], params['extra_params'],
                                             self.root.ui_update_queue.put)
            elif strat_type == "Technical":
                new_strat = TechnicalStrategy(self, contract, self.platform, tf,
                                              params['balance_pct'], params['take_profit'],
                                              params['stop_loss'], params['extra_params'],
                                              self.root.ui_update_queue.put)
            elif strat_type == "Breakout":
                new_strat = BreakoutStrategy(self, contract, self.platform, tf,
                                             params['balance_pct'], params['take_profit'],
                                             params['stop_loss'], params['extra_params'],
                                             self.root.ui_update_queue.put)
            else:
                logger.error(f"BinanceClient: Unknown strategy type: {strat_type}")
                return

            # Attach the candles we downloaded in Step 4
            new_strat.candles = historical_candles

        except Exception as e:
            logger.error(f"BinanceClient: Strategy instantiation error: {e}")
            return

        # 7. Thread-Safe Storage (INSIDE THE LOCK - very brief)
        with self.strategies_lock:
            self.strategies[strat_id] = new_strat

        # 8. Start Real-time Data Subscriptions
        # These are multiplexed stream requests
        self.subscribe_channel([contract], "bookticker")
        self.subscribe_channel([contract], "aggtrade")
        self.subscribe_channel([contract], "kline", interval=tf)

        logger.info(f"BinanceClient: Successfully started {strat_id} at row {b_index}")

    def remove_strategy(self, symbol: str, tf: str, strat_type: str):
        """
        Instantly removes a strategy using its unique components.
        """
        strat_id = f"{symbol}_{tf}_{strat_type}"

        with self.strategies_lock:
            if strat_id in self.strategies:
                # 1. Stop the strategy's internal logic immediately
                self.strategies[strat_id].dead = True

                # 2. Delete from dictionary
                del self.strategies[strat_id]

                logger.info(f"BinanceClient: Strategy {strat_id} successfully removed.")
            else:
                logger.warning(f"BinanceClient: Attempted to remove non-existent strategy {strat_id}")