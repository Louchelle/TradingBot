# models/worker.py

import threading
import logging
import time
import queue

logger = logging.getLogger()

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from connectors.binance_client import BinanceClient
    from connectors.bitmex import BitmexClient

class Worker(threading.Thread):
    """
    The Worker class runs as a separate thread. Its main job is to:
    1. Receive all market data and order updates from the clients (BinanceClient).
    2. Run the strategies (IchimokuStrategy).
    3. Place new orders via the client.
    4. Pass non-critical UI updates (like logs) to the Root component.
    """

    def __init__(self,
                 binance_futures_client: 'BinanceClient',
                 order_status_queue: queue.Queue,
                 ui_update_queue: queue.Queue,
                 pnl_update_queue: queue.Queue,
                 binance_spot_client: 'BinanceClient' = None,
                 bitmex_client: 'BitmexClient' = None):


        super().__init__()
        self.daemon = True  # Allows the program to exit even if this thread is still running

        # Store the clients
        self.binance = binance_futures_client
        self.binance_spot = binance_spot_client
        self.bitmex = bitmex_client

        # Communication Queues
        self.order_status_queue = order_status_queue
        self.ui_update_queue = ui_update_queue
        self.pnl_update_queue = pnl_update_queue

        self.task_queue = queue.Queue()

        # Control flag
        self._should_stop = threading.Event()

    def run(self):
        logger.info("Worker thread started.")

        self.task_queue.put(("RELOAD_DB", None, None))

        while not self._should_stop.is_set():
            # --- 1. PROCESS THE TASK QUEUE (Commands like RELOAD_DB) ---
            try:
                # We check the task queue first.
                # This is where 'RELOAD_DB' from main.py will arrive.
                task = self.task_queue.get(block=False)
                action, data, extra = task

                if action == "RELOAD_DB":
                    self._handle_db_recovery()
                elif action == "ADD_STRATEGY":
                    # Data is our dict, extra is the b_index for UI updates
                    self._handle_add_strategy(data, extra)
                elif action == "STOP_STRATEGY":
                    # ADD THIS BRANCH:
                    self._handle_stop_strategy(extra)  # extra is the b_index

                # If you have other background tasks, add 'elif' here

                self.task_queue.task_done()

            except queue.Empty:
                pass

            # --- 2. PROCESS ORDER STATUS UPDATES (Market Events) ---
            try:
                update = self.order_status_queue.get(block=False)
                logger.info(f"Worker received order update: {update}")
                # Logic to route update to the correct strategy...

                self.order_status_queue.task_done()
            except queue.Empty:
                pass

            # Heartbeat sleep to prevent 100% CPU usage
            time.sleep(0.1)

        logger.info("Worker thread stopped gracefully.")

    def _handle_db_recovery(self):
        logger.info("Worker: Starting background synchronization...")

        # --- 1. SYNC BINANCE FUTURES (with retry for 502 errors) ---
        while not self.binance.is_ready:
            try:
                print("DEBUG: Worker is attempting to fetch contracts...", flush=True)
                # This triggers get_contracts() and get_balances()
                self.binance.connect()

                if len(self.binance.contracts) > 0:
                    self.binance.is_ready = True
                    logger.info("Worker: Binance Futures synchronized successfully.")
                else:
                    raise Exception("Connected, but no contracts were returned.")
            except Exception as e:
                print(f"DEBUG: Worker failed! Error: {e}", flush=True)
                logger.error(f"Worker: Binance Futures down (502/Timeout). Retrying in 10s... {e}")
                time.sleep(10)

        # --- 2. CONNECT OTHER CLIENTS (Independently) ---
        try:
            if self.bitmex:
                if hasattr(self.bitmex, 'connect'):
                    self.bitmex.connect()
                else:
                    logger.info("Worker: Bitmex client skipping manual connect().")

            if self.binance_spot:
                self.binance_spot.connect()
                logger.info("Worker: Binance Spot synchronized.")

        except Exception as e:
            logger.error(f"Worker: Secondary connection sync failed: {e}")

        # --- 3. RESTORE STRATEGIES ---
        from database import WorkspaceData
        db = WorkspaceData()
        saved_strategies = db.get("strategies")

        for s in saved_strategies:
            self.ui_update_queue.put(("RESTORE_STRATEGY", s))

        logger.info(f"Worker: Recovery complete. Restored {len(saved_strategies)} strategies.")

    def stop(self):
        """
        Sets the internal flag to stop the thread gracefully.
        """
        self._should_stop.set()

    def _handle_add_strategy(self, params: dict, b_index: int):
        def start_task():
            try:
                # 1. Start the strategy (this triggers the candle fetch)
                self.binance.start_strategy(params, b_index)

                # REMOVE this line below from worker.py!
                self.ui_update_queue.put(("STRATEGY_ON", b_index))

                # The strategy itself will now send "STRATEGY_ON" via its
                # ui_callback once it passes the 1000-candle Data Guard.

            except Exception as e:
                logger.error(f"Worker Error: {e}")
                self.ui_update_queue.put(("STRATEGY_OFF", b_index))

        threading.Thread(target=start_task, daemon=True).start()

    def _handle_stop_strategy(self, params: dict):

        def stop_task():
            try:
                symbol = params.get('contract') or params.get('contract_str')
                tf = params.get('timeframe')
                strat_type = params.get('strategy_type')
                b_index = params.get('row_index')

                # Call the updated remove_strategy in BinanceClient
                self.binance.remove_strategy(symbol, tf, strat_type)

                # Signal the GUI using the row_index so the button flips back to "OFF"
                self.ui_update_queue.put(("STRATEGY_OFF", b_index))

            except Exception as e:
                logger.error(f"Worker: Error stopping strategy: {e}")

        threading.Thread(target=stop_task, daemon=True).start()