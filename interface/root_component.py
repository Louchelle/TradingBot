import queue
import threading
import time
import tkinter as tk
from tkinter.messagebox import askquestion
import logging
import json
from typing import Any

from connectors.bitmex import BitmexClient
from connectors.binance_client import BinanceClient

from interface.styling import *
from interface.logging_component import Logging
from interface.watchlist_component import Watchlist
from interface.trades_component import TradesWatch
from interface.strategy_component import StrategyEditor

from models.models import Trade

logger = logging.getLogger()


class Root(tk.Tk):
    def __init__(self, binance, bitmex, binance_spot, worker,
                 order_status_queue, ui_update_queue, pnl_update_queue):

        super().__init__()

        self.binance = binance
        self.bitmex = bitmex
        self.binance_spot = binance_spot
        self.worker = worker

        # --- PnL Callbacks ---
        if self.binance:
            self.binance.pnl_update_callback = self.notify_trade_pnl_update
        if self.bitmex:
            self.bitmex.pnl_update_callback = self.notify_trade_pnl_update
        if self.binance_spot:
            self.binance_spot.pnl_update_callback = self.notify_trade_pnl_update

        self.ui_update_queue = ui_update_queue
        self.order_status_queue = order_status_queue
        self.pnl_update_queue = pnl_update_queue

        # Start Queue Processing
        self.after(100, self._check_ui_queue)
        self._check_order_status_queue()
        self._check_pnl_queue()

        self.title("Trading Bot")
        self.protocol("WM_DELETE_WINDOW", self._ask_before_close)
        self.configure(bg=BG_COLOR)

        # --- MENU ---
        self.main_menu = tk.Menu(self)
        self.configure(menu=self.main_menu)
        self.workspace_menu = tk.Menu(self.main_menu, tearoff=False)
        self.main_menu.add_cascade(label="Workspace", menu=self.workspace_menu)
        self.workspace_menu.add_command(label="Save workspace", command=self._save_workspace)

        # --- MAIN WINDOW GRID CONFIGURATION ---
        self.grid_columnconfigure(0, weight=1, minsize=300)
        self.grid_columnconfigure(1, weight=4)
        self.grid_rowconfigure(0, weight=1)

        # Main Container Frames
        self._left_frame = tk.Frame(self, bg=BG_COLOR)
        self._left_frame.grid(row=0, column=0, sticky="nsew")

        self._right_frame = tk.Frame(self, bg=BG_COLOR)
        self._right_frame.grid(row=0, column=1, sticky="nsew")

        # --- LEFT FRAME GRID CONFIGURATION ---
        self._left_frame.grid_rowconfigure(0, weight=1)
        self._left_frame.grid_rowconfigure(1, weight=1)
        self._left_frame.grid_columnconfigure(0, weight=1)

        binance_contracts = self.binance.contracts if self.binance else {}
        bitmex_contracts = self.bitmex.contracts if self.bitmex else {}
        binance_spot_contracts = self.binance_spot.contracts if self.binance_spot else {}

        self._watchlist_frame = Watchlist(self._left_frame, binance_contracts, bitmex_contracts,
                                          binance_spot_contracts, bg=BG_COLOR)
        self._watchlist_frame.grid(row=0, column=0, sticky="nsew")

        self.logging_frame = Logging(self._left_frame, bg=BG_COLOR)
        self.logging_frame.grid(row=1, column=0, sticky="nsew")

        # --- RIGHT FRAME GRID CONFIGURATION ---
        self._right_frame.grid_rowconfigure(0, weight=2)
        self._right_frame.grid_rowconfigure(1, weight=1)
        self._right_frame.grid_columnconfigure(0, weight=1)

        self._strategy_editor = StrategyEditor(self, self._right_frame, binance=self.binance,
                                               bitmex=self.bitmex, binance_spot=self.binance_spot,
                                               bg=BG_COLOR)
        self._strategy_editor.grid(row=0, column=0, sticky="nsew")

        self._trades_frame = TradesWatch(self, self._right_frame, bg=BG_COLOR)
        self._trades_frame.grid(row=1, column=0, sticky="nsew")

        # --- STATUS BAR ---
        self.status_var = tk.StringVar(value="Status: Synchronizing Market Data...")
        self.status_bar = tk.Label(self, textvariable=self.status_var, bd=1,
                                   relief=tk.SUNKEN, anchor=tk.W,
                                   bg=BG_COLOR, fg=FG_COLOR, font=GLOBAL_FONT)
        self.status_bar.grid(row=2, column=0, columnspan=2, sticky="we")

        # Post-Init
        self.after(500, lambda: self.logging_frame.add_log("GUI Initialized. Waiting for Worker..."))
        self._update_ui()
        self.after(1000, self._start_threads)
        self.after(2000, self._check_worker_status)

    # --- QUEUE & UI LOGIC ---

    def _check_ui_queue(self):
        """Processes UI updates without blocking the main thread."""
        try:
            for _ in range(30):  # Cap per cycle to prevent freezing
                item = self.ui_update_queue.get_nowait()
                tag, data = item
                if tag == "RESTORE_STRATEGY":
                    self._strategy_editor._add_strategy_row(data)
                else:
                    self.process_ui_update(tag, data)
                self.ui_update_queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.after(100, self._check_ui_queue)

    def process_ui_update(self, tag: str, data: Any):
        if tag == "STRATEGY_ON":
            try:
                b_index = int(data)
                if b_index in self._strategy_editor.body_widgets['activation']:
                    self._strategy_editor.body_widgets['activation'][b_index].config(
                        text="LIVE", bg="darkgreen", fg="white", state="normal"
                    )
                logger.info(f"GUI: Strategy at row {b_index} is now LIVE.")
            except (ValueError, TypeError):
                pass
            return

        elif tag == "STRATEGY_OFF":
            try:
                b_index = int(data)
                if b_index in self._strategy_editor.body_widgets['activation']:
                    self._strategy_editor.body_widgets['activation'][b_index].config(
                        text="OFF", bg="darkred", fg="white", state="normal"
                    )
                logger.info(f"GUI: Strategy at row {b_index} is now OFFLINE.")
            except (ValueError, TypeError):
                pass
            return

        parts = tag.split(":", 1)
        if parts[0] == "LOG":
            strategy_name = parts[1] if len(parts) > 1 else "SYSTEM"
            self.logging_frame.add_log(f"[{strategy_name}] {data}")

    def _update_ui(self):
        """High-frequency loop for PnL and Watchlist updates."""
        for client in [self.binance, self.binance_spot, self.bitmex]:
            if not client: continue
            try:
                with client.strategies_lock:
                    all_trades = []
                    for strat in client.strategies.values():
                        all_trades.extend(strat.trades)
                    if hasattr(client, 'active_trades'):
                        all_trades.extend(client.active_trades)

                for trade in all_trades:
                    if trade.status == "open" and trade.symbol in client.prices:
                        prices = client.prices[trade.symbol]
                        exit_price = prices['bid'] if trade.side.lower() == "long" else prices['ask']
                        mult = getattr(trade.contract, 'multiplier', 1.0) or 1.0

                        # Calculate PnL with absolute quantity for safety
                        qty = abs(float(trade.quantity))
                        if trade.side.lower() == "long":
                            trade.pnl = (exit_price - trade.entry_price) * qty * mult
                        else:
                            trade.pnl = (trade.entry_price - exit_price) * qty * mult

                        self.notify_trade_pnl_update(trade)
            except Exception as e:
                logger.error(f"PnL Update Error: {e}")

        self.after(250, self._update_ui)

    def notify_trade_pnl_update(self, trade):
        def update_gui():
            try:
                target_id = None
                if trade.time in self._trades_frame.body_widgets['symbol']:
                    target_id = trade.time
                else:
                    for tid, widget in self._trades_frame.body_widgets['symbol'].items():
                        if widget.cget("text").upper() == trade.symbol.upper():
                            target_id = tid
                            break
                if target_id is not None:
                    prec = getattr(trade.contract, 'price_decimals', 2)
                    self._trades_frame.body_widgets['pnl_var'][target_id].set(f"{trade.pnl:.{prec}f}")
                    self._trades_frame.body_widgets['quantity_var'][target_id].set(trade.quantity)
            except Exception:
                pass

        self.after(0, update_gui)

    # --- WORKER & THREADING ---

    def _check_worker_status(self):
        if self.binance and len(self.binance.contracts) > 0:
            logger.info("Worker sync confirmed. Updating UI...")
            self.status_var.set("Status: Ready")
            self._strategy_editor._all_contracts = sorted(list(self.binance.contracts.keys()))
            self._strategy_editor.update_contracts_menu()
            self._strategy_editor.load_strategies()
        else:
            self.after(1000, self._check_worker_status)

    def _start_threads(self):
        for client in [self.binance, self.bitmex, self.binance_spot]:
            if client and hasattr(client, 'start_ws_thread'):
                if not getattr(client, 'ws_connected', False):
                    client.start_ws_thread()

    def _run_sync_logic(self):
        """Background Thread: Fetches existing positions."""
        time.sleep(2)
        if not self.binance or not self.binance.futures: return
        try:
            open_positions = self.binance.get_open_positions()
            symbols_to_subscribe = []
            for pos in open_positions:
                symbol = pos['symbol']
                contract_obj = self.binance.contracts.get(symbol)
                if not contract_obj: continue

                existing_ui_symbols = [w.cget("text") for w in self._trades_frame.body_widgets['symbol'].values()]
                if symbol not in existing_ui_symbols:
                    raw_side = pos['side'].upper()
                    normalized_side = "long" if raw_side in ["BUY", "LONG"] else "short"
                    trade_data = {
                        'time': int(time.time() * 1000), 'symbol': symbol, 'side': normalized_side,
                        'entry_price': float(pos['entry_price']), 'size': abs(float(pos['size'])),
                        'status': 'open', 'pnl': float(pos['pnl']), 'strategy': 'Manual Sync',
                    }
                    new_trade = Trade(trade_data, contract_obj)
                    self.binance.active_trades.append(new_trade)
                    self.after(0, self._trades_frame.add_trade, new_trade)
                    symbols_to_subscribe.append(contract_obj)

            if symbols_to_subscribe:
                self.binance.subscribe_channel(symbols_to_subscribe, "bookticker")
                self.after(0, lambda: self.logging_frame.add_log(f"Sync: Connected {len(symbols_to_subscribe)} feeds."))
        except Exception as e:
            logger.error(f"Sync Logic Error: {e}")

    def trigger_sync(self):
        threading.Thread(target=self._run_sync_logic, daemon=True).start()

    # --- HELPERS ---

    def manual_close(self, trade: Trade):
        self.logging_frame.add_log(f"Sending close order for {trade.symbol}...")
        try:
            val = trade.quantity.get() if hasattr(trade.quantity, 'get') else trade.quantity
            qty = abs(float(val if str(val).strip().upper() != "N/A" else getattr(trade, 'quantity', 0)))
            side = "SELL" if trade.side.lower() == "long" else "BUY"
            if self.binance:
                return self.binance.place_order(trade.contract, "MARKET", qty, side)
        except Exception as e:
            logger.error(f"Manual close Error: {e}")
            self.logging_frame.add_log(f"Error: {e}")
            return None

    def _save_workspace(self):
        # Watchlist
        watchlist_symbols = []
        for key, value in self._watchlist_frame.body_widgets['symbol'].items():
            symbol = value.cget("text")
            exchange = self._watchlist_frame.body_widgets['exchange'][key].cget("text")
            watchlist_symbols.append((symbol, exchange,))
        self._watchlist_frame.db.save("watchlist", watchlist_symbols)

        # Strategies
        strategies = []
        strat_widgets = self._strategy_editor.body_widgets
        for b_index in strat_widgets['contract']:
            strategy_type = strat_widgets['strategy_type_var'][b_index].get()
            contract_raw = strat_widgets['contract_var'][b_index].get()
            contract = contract_raw.rsplit("_", 1)[0] + "_" + contract_raw.rsplit("_", 1)[
                1].lower() if "_" in contract_raw else contract_raw

            strategies.append((
                strategy_type, contract, strat_widgets['timeframe_var'][b_index].get(),
                strat_widgets['balance_pct'][b_index].get(), strat_widgets['take_profit'][b_index].get(),
                strat_widgets['stop_loss'][b_index].get(),
                json.dumps(
                    {p['code_name']: self._strategy_editor.additional_parameters.get(b_index, {}).get(p['code_name'])
                     for p in self._strategy_editor.extra_params[strategy_type]}),
                1 if strat_widgets['activation'][b_index].cget("text") != "OFF" else 0
            ))
        self._strategy_editor.db.save("strategies", strategies)
        self.logging_frame.add_log("Workspace saved")

    def _ask_before_close(self):
        if askquestion("Confirmation", "Do you really want to exit?") == "yes":
            if self.worker: self.worker.stop()
            for client in [self.binance, self.bitmex, self.binance_spot]:
                if client:
                    client.reconnect = False
                    if hasattr(client, 'ws') and client.ws: client.ws.close()
            self.destroy()

    def _check_order_status_queue(self):
        try:
            for _ in range(15):
                item = self.order_status_queue.get_nowait()
                self._trades_frame.update_trade_log(item)
                self.order_status_queue.task_done()
        except queue.Empty:
            pass
        self.after(200, self._check_order_status_queue)

    def _check_pnl_queue(self):
        try:
            for _ in range(15):
                trade = self.pnl_update_queue.get_nowait()
                self.notify_trade_pnl_update(trade)
                self.pnl_update_queue.task_done()
        except queue.Empty:
            pass
        self.after(100, self._check_pnl_queue)