import tkinter as tk
from tkinter import messagebox
import typing
import json
import logging
import threading
from functools import partial

from interface.styling import *
from interface.scrollable_frame import ScrollableFrame

from connectors.binance_client import BinanceClient
from connectors.bitmex import BitmexClient

from strategies import TechnicalStrategy, BreakoutStrategy, IchimokuStrategy
from utils import *

from database import WorkspaceData

logger = logging.getLogger()

if typing.TYPE_CHECKING:
    from interface.root_component import Root


class StrategyEditor(tk.Frame):
    def __init__(self, root: "Root", master: tk.Frame, binance: BinanceClient, bitmex: BitmexClient,
                 binance_spot: BinanceClient, **kwargs):

        super().__init__(master, **kwargs)

        self.root: "Root" = root

        self.binance = binance
        self.bitmex = bitmex
        self.binance_spot = binance_spot

        self.db = WorkspaceData()

        self._valid_integer = self.register(check_integer_format)
        self._valid_float = self.register(check_float_format)

        self._exchanges = {}
        if self.binance is not None:
            self._exchanges["binance_futures"] = self.binance
        if self.binance_spot is not None:
            self._exchanges["binance_spot"] = self.binance_spot
        if self.bitmex is not None:
            self._exchanges["bitmex"] = self.bitmex

        self._all_contracts = []
        self._all_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h"]

        for exchange, client in self._exchanges.items():
            if client is not None and hasattr(client, 'contracts'):
                for symbol in client.contracts.keys():
                    clean_symbol = symbol.split("_")[0].upper()
                    formatted_contract = f"{clean_symbol}_{exchange}"

                    if formatted_contract not in self._all_contracts:
                        self._all_contracts.append(formatted_contract)

        self._commands_frame = tk.Frame(self, bg=BG_COLOR)
        self._commands_frame.pack(side=tk.TOP)

        self._table_frame = tk.Frame(self, bg=BG_COLOR)
        self._table_frame.pack(side=tk.TOP)

        self._add_button = tk.Button(self._commands_frame, text="Add strategy", font=GLOBAL_FONT,
                                     command=self._add_strategy_row, bg=BG_COLOR_2, fg=FG_COLOR)
        self._add_button.pack(side=tk.LEFT, padx=10)

        self._kill_button = tk.Button(self._commands_frame, text="STOP ALL", font=GLOBAL_FONT,
                                      command=self._kill_all_strategies, bg="darkred", fg=FG_COLOR)
        self._kill_button.pack(side=tk.LEFT, padx=10)

        self.body_widgets = dict()
        self.additional_parameters = dict()
        self._extra_input = dict()

        self._base_params = [
            {"code_name": "strategy_type", "widget": tk.OptionMenu, "data_type": str,
             "values": ["Technical", "Breakout", "Ichimoku"], "width": 10, "header": "Strategy"},
            {"code_name": "contract", "widget": tk.OptionMenu, "data_type": str, "values": self._all_contracts,
             "width": 15, "header": "Contract"},
            {"code_name": "timeframe", "widget": tk.OptionMenu, "data_type": str, "values": self._all_timeframes,
             "width": 10, "header": "Timeframe"},
            {"code_name": "balance_pct", "widget": tk.Entry, "data_type": float, "width": 10, "header": "Balance %"},
            {"code_name": "take_profit", "widget": tk.Entry, "data_type": float, "width": 7, "header": "TP %"},
            {"code_name": "stop_loss", "widget": tk.Entry, "data_type": float, "width": 7, "header": "SL %"},
            {"code_name": "parameters", "widget": tk.Button, "data_type": str, "text": "Parameters",
             "bg": BG_COLOR_2, "command": self._show_popup, "header": "", "width": 10},
            {"code_name": "activation", "widget": tk.Button, "data_type": str, "text": "OFF",
             "bg": "darkred", "command": self._switch_strategy, "header": "", "width": 8},
            {"code_name": "delete", "widget": tk.Button, "data_type": str, "text": "X",
             "bg": "darkred", "command": self._delete_row, "header": "", "width": 6},
        ]

        self.extra_params = {
            "Technical": [
                {"code_name": "rsi_length", "name": "RSI Periods", "widget": tk.Entry, "data_type": int},
                {"code_name": "ema_fast", "name": "MACD Fast Length", "widget": tk.Entry, "data_type": int},
                {"code_name": "ema_slow", "name": "MACD Slow Length", "widget": tk.Entry, "data_type": int},
                {"code_name": "ema_signal", "name": "MACD Signal Length", "widget": tk.Entry, "data_type": int},
            ],
            "Breakout": [
                {"code_name": "min_volume", "name": "Minimum Volume", "widget": tk.Entry, "data_type": float},
                {"code_name": "window", "name": "Breakout Window (30)", "widget": tk.Entry, "data_type": int},
            ],
            "Ichimoku": [
                {"code_name": "tenkan", "name": "Tenkan-sen Periods", "widget": tk.Entry, "data_type": int},
                {"code_name": "kijun", "name": "Kijun-sen Periods", "widget": tk.Entry, "data_type": int},
                {"code_name": "senkou", "name": "Senkou Span B Periods", "widget": tk.Entry, "data_type": int},
            ]
        }

        for h in self._base_params:
            self.body_widgets[h['code_name']] = dict()
            if h['code_name'] in ["strategy_type", "contract", "timeframe"]:
                self.body_widgets[h['code_name'] + "_var"] = dict()

        self._headers_frame = tk.Frame(self._table_frame, bg=BG_COLOR)
        for idx, h in enumerate(self._base_params):
            header = tk.Label(self._headers_frame, text=h['header'], bg=BG_COLOR, fg=FG_COLOR, font=GLOBAL_FONT,
                              width=h['width'], bd=1, relief=tk.FLAT)
            header.grid(row=0, column=idx, padx=2)
        self._headers_frame.pack(side=tk.TOP, anchor="nw")

        self._body_frame = ScrollableFrame(self._table_frame, bg=BG_COLOR, height=250)
        self._body_frame.pack(side=tk.TOP, fill=tk.X, anchor="nw")

        self._body_index = 0

    def _add_strategy_row(self, data=None):

        # 1. Block manual empty rows while contracts are loading
        # 2. ALLOW database-driven rows (data is NOT None) to bypass this check
        if not self._all_contracts and data is None:
            logger.warning("Try to add row, but contracts not loaded yet.")
            return

            # Handle duplicates during auto-restore
        if data is not None:
            new_contract = data.get('contract')
            new_tf = data.get('timeframe')
            for b_index, contract_var in self.body_widgets['contract_var'].items():
                if contract_var.get() == new_contract and self.body_widgets['timeframe_var'][b_index].get() == new_tf:
                    logger.info(f"Ignored duplicate UI request for {new_contract} {new_tf}")
                    return

        # Increment and create row...
        while self._body_index in self.body_widgets['activation']:
            self._body_index += 1

        b_index = self._body_index

        for col, h in enumerate(self._base_params):
            code_name = h['code_name']

            if h['widget'] == tk.OptionMenu:
                self.body_widgets[code_name + "_var"][b_index] = tk.StringVar()

                if code_name == "contract":
                    options = self._all_contracts if self._all_contracts else ["None"]
                elif code_name == "timeframe":
                    # Use the class variable self._all_timeframes
                    options = self._all_timeframes if self._all_timeframes else ["1m", "5m", "15m", "1h"]
                else:
                    options = h['values'] if len(h['values']) > 0 else ["None"]

                self.body_widgets[code_name + "_var"][b_index].set(options[0])

                self.body_widgets[code_name][b_index] = tk.OptionMenu(
                    self._body_frame.sub_frame, self.body_widgets[code_name + "_var"][b_index], *options
                )
                self.body_widgets[code_name][b_index].config(
                    width=h['width'], bd=0, indicatoron=0, bg=BG_COLOR, fg=FG_COLOR,
                    activebackground=BG_COLOR_2, activeforeground=FG_COLOR
                )

            elif h['widget'] == tk.Entry:
                self.body_widgets[code_name][b_index] = tk.Entry(
                    self._body_frame.sub_frame, justify=tk.CENTER, font=GLOBAL_FONT,
                    width=h['width'], bd=1, bg=BG_COLOR_2, fg=FG_COLOR, insertbackground=FG_COLOR
                )
                if code_name == "balance_pct":
                    self.body_widgets[code_name][b_index].insert(0, "0.0")
                elif code_name in ["take_profit", "stop_loss"]:
                    self.body_widgets[code_name][b_index].insert(0, "2.0")

            elif h['widget'] == tk.Button:
                self.body_widgets[code_name][b_index] = tk.Button(
                    self._body_frame.sub_frame,
                    text=h['text'],
                    font=GLOBAL_FONT,
                    bg=h['bg'],
                    fg=FG_COLOR,
                    width=h['width'],
                    command=lambda cmd=h['command'], b_idx=b_index: cmd(b_idx)
                )

            self.body_widgets[code_name][b_index].grid(row=b_index, column=col, padx=2, pady=2)

        self._body_index += 1
        return b_index

    def _show_popup(self, b_index: int):
        if b_index not in self.body_widgets['strategy_type_var']:
            return

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self._popup_window = tk.Toplevel(self)
        self._popup_window.wm_title("Parameters")
        self._popup_window.config(bg=BG_COLOR)
        self._popup_window.geometry(f"+{x + 400}+{y + 200}")

        strat_selected = self.body_widgets['strategy_type_var'][b_index].get()
        self.additional_parameters.setdefault(b_index, {})

        for idx, param in enumerate(self.extra_params[strat_selected]):
            code_name = param['code_name']
            tk.Label(self._popup_window, text=param['name'], bg=BG_COLOR, fg=FG_COLOR, font=GLOBAL_FONT).grid(row=idx,
                                                                                                              column=0)
            self._extra_input[code_name] = tk.Entry(self._popup_window, bg=BG_COLOR_2, justify=tk.CENTER, fg=FG_COLOR,
                                                    font=GLOBAL_FONT)

            saved_val = self.additional_parameters[b_index].get(code_name)
            if saved_val is not None:
                self._extra_input[code_name].insert(0, str(saved_val))
            elif strat_selected == "Breakout" and code_name == "window":
                self._extra_input[code_name].insert(0, "30")

            self._extra_input[code_name].grid(row=idx, column=1, padx=2, pady=2)

        tk.Button(self._popup_window, text="Validate", bg=BG_COLOR_2, fg=FG_COLOR,
                  command=lambda: self._validate_parameters(b_index)).grid(row=len(self.extra_params[strat_selected]),
                                                                           column=0, columnspan=2)

    def _validate_parameters(self, b_index: int):
        strat_selected = self.body_widgets['strategy_type_var'][b_index].get()
        for param in self.extra_params[strat_selected]:
            code_name = param['code_name']
            try:
                val = param['data_type'](self._extra_input[code_name].get())
                self.additional_parameters[b_index][code_name] = val
            except ValueError:
                continue
        self._popup_window.destroy()

    def _switch_strategy(self, b_index: int):
        # 1. Get current text to determine action
        current_status = self.body_widgets['activation'][b_index].cget("text")

        # SAFETY: If it's already "STARTING", ignore the click to prevent duplicate candle fetching
        if current_status == "STARTING":
            return

        # 2. Collect Data (Common for both states)
        try:
            strategy_type = self.body_widgets['strategy_type_var'][b_index].get()
            contract_str = self.body_widgets['contract_var'][b_index].get()
            timeframe = self.body_widgets['timeframe_var'][b_index].get()
            balance_pct = float(self.body_widgets['balance_pct'][b_index].get())
            take_profit = float(self.body_widgets['take_profit'][b_index].get())
            stop_loss = float(self.body_widgets['stop_loss'][b_index].get())

            # Ensure we have the latest extra parameters (Ichimoku/Breakout specific)
            extra_params = self.additional_parameters.get(b_index, {})

        except (ValueError, KeyError) as e:
            messagebox.showerror("Invalid Input", f"Ensure all fields are filled correctly: {e}")
            return

        if current_status == "OFF":
            
            extra_params['row_index'] = b_index

            # --- START LOGIC ---
            strat_data = {
                "strategy_type": strategy_type,
                "contract_str": contract_str,
                "timeframe": timeframe,
                "balance_pct": balance_pct,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "extra_params": extra_params
            }

            # Set status to STARTING while Worker is fetching 1000 candles
            self.body_widgets['activation'][b_index].config(text="STARTING", bg="orange")

            # Send to Worker
            self.root.worker.task_queue.put(("ADD_STRATEGY", strat_data, b_index))

            # Save to DB as Active (1)
            self.db.save("strategies", [(strategy_type, contract_str, timeframe, balance_pct,
                                         take_profit, stop_loss, json.dumps(extra_params), 1)])

        elif current_status in ["LIVE", "ONLINE"]:  # Check for both possible "on" labels
            # --- STOP LOGIC ---

            # Put the stop command in the queue
            self.root.worker.task_queue.put(("STOP_STRATEGY",
                                             {"contract_str": contract_str, "tf": timeframe},
                                             b_index))

            # Immediately update UI to show it's shutting down
            self.body_widgets['activation'][b_index].config(text="OFF", bg="darkred")

            # Save to DB as Inactive (0)
            self.db.save("strategies", [(strategy_type, contract_str, timeframe, balance_pct,
                                         take_profit, stop_loss, json.dumps(extra_params), 0)])

    def _delete_row(self, b_index: int):
        if b_index not in self.body_widgets['activation']: return
        contract_str = self.body_widgets['contract_var'][b_index].get()
        timeframe = self.body_widgets['timeframe_var'][b_index].get()

        if not messagebox.askyesno("Confirm", f"Delete {contract_str} permanently?"):
            return

        if self.body_widgets['activation'][b_index].cget("text") != "OFF":
            self._switch_strategy(b_index)

        try:
            self.db.delete_strategy(contract_str, timeframe)
        except Exception as e:
            logger.error(f"DB Delete Error: {e}")

        for key in list(self.body_widgets.keys()):
            if b_index in self.body_widgets[key]:
                widget = self.body_widgets[key][b_index]
                if hasattr(widget, "destroy"): widget.destroy()
                del self.body_widgets[key][b_index]

    def load_strategies(self):

        data = self.db.get("strategies")

        for i, row in enumerate(data):
            # 1. Add the row and capture the specific index for this row
            b_index = self._add_strategy_row(data=row)

            # 2. Only proceed if a row was successfully created (not a duplicate/error)
            if b_index is not None:
                # Set the Strategy Type
                self.body_widgets['strategy_type_var'][b_index].set(row['strategy_type'])

                # Handle Contract naming
                raw_contract = row['contract']
                symbol = raw_contract.split("_")[0].upper()
                if "binance" in raw_contract.lower():
                    clean_contract = f"{symbol}_binance_futures"
                elif "bitmex" in raw_contract.lower():
                    clean_contract = f"{symbol}_bitmex"
                else:
                    clean_contract = raw_contract

                # Set Contract and Timeframe
                self.body_widgets['contract_var'][b_index].set(clean_contract)
                self.body_widgets['timeframe_var'][b_index].set(row['timeframe'])

                # Populate numeric fields
                for field in ['balance_pct', 'take_profit', 'stop_loss']:
                    self.body_widgets[field][b_index].delete(0, tk.END)

                    val = row[field] if row[field] is not None else ""
                    self.body_widgets[field][b_index].insert(0, val)

                # Load Extra Parameters
                if row['extra_params']:
                    try:
                        self.additional_parameters[b_index] = json.loads(row['extra_params'])
                    except json.JSONDecodeError:
                        logger.error(f"Failed to load extra_params for row {b_index}")

                # Auto-Resume logic
                if row.get('is_active') == 1:
                    delay = 15000 + (i * 1000)
                    self.after(delay, lambda idx=b_index: self._switch_strategy(idx))

    def _kill_all_strategies(self):
        for b_index in list(self.body_widgets['activation'].keys()):
            if self.body_widgets['activation'][b_index].cget("text") != "OFF":
                self._switch_strategy(b_index)

    def update_contracts_menu(self):
        if not self._all_contracts:
            logger.warning("Update called but _all_contracts is empty.")
            return

        self._all_contracts.sort()

        # Debug: Check how many rows we are updating
        # logger.info(f"Updating {len(self.body_widgets['contract'])} rows with {len(self._all_contracts)} contracts.")

        for b_index, menu_widget in self.body_widgets['contract'].items():
            try:
                menu_content = menu_widget["menu"]
                menu_content.delete(0, "end")

                for symbol in self._all_contracts:
                    menu_content.add_command(
                        label=symbol,
                        command=lambda val=symbol, idx=b_index: self.body_widgets['contract_var'][idx].set(val)
                    )

                # Auto-select the first one if current is "None" or empty
                current = self.body_widgets['contract_var'][b_index].get()
                if current == "None" or current == "":
                    self.body_widgets['contract_var'][b_index].set(self._all_contracts[0])

            except Exception as e:
                logger.error(f"Failed to update menu at index {b_index}: {e}")