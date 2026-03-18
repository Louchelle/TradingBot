import tkinter as tk
import logging
import threading

from models.models import *

from interface.styling import *
from interface.scrollable_frame import ScrollableFrame

logger = logging.getLogger()


class TradesWatch(tk.Frame):
    def __init__(self, root, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)

        self.root = root

        # 1. Align headers with the data you actually show in add_trade()
        # I added "Exchange" and swapped order to match your add_trade logic
        self._headers = ["Time", "Symbol", "Exchange", "Strategy", "Side", "Quantity", "Status", "PnL", "Action"]
        self._column_weights = [2, 2, 2, 2, 1, 2, 2, 2, 1]

        # 2. Initialize body_widgets properly.
        # We create a dictionary for every header to store the labels/vars.
        self.body_widgets = dict()
        for h in self._headers:
            self.body_widgets[h.lower()] = dict()
            # Create sub-dictionaries for dynamic variables
            if h.lower() in ["status", "pnl", "quantity"]:
                self.body_widgets[h.lower() + "_var"] = dict()

        self._table_frame = tk.Frame(self, bg=BG_COLOR)
        self._table_frame.pack(side=tk.TOP, fill=tk.X)

        self._col_width = 12

        # 3. Headers UI
        self._headers_frame = tk.Frame(self._table_frame, bg=BG_COLOR)
        for idx, h in enumerate(self._headers):
            header = tk.Label(self._headers_frame, text=h, bg=BG_COLOR,
                              fg=FG_COLOR, font=GLOBAL_FONT, width=self._col_width)
            header.grid(row=0, column=idx)

        # Space for scrollbar
        tk.Label(self._headers_frame, text="", bg=BG_COLOR, width=2).grid(row=0, column=len(self._headers))
        self._headers_frame.pack(side=tk.TOP, anchor="nw")

        # 4. Body UI
        self._body_frame = ScrollableFrame(self, bg=BG_COLOR, height=250)
        self._body_frame.pack(side=tk.TOP, anchor="nw", fill=tk.X)

        self._body_index = 0

    def add_trade(self, trade: Trade):

        """
        Add a new trade row to the UI.
        """
        b_index = self._body_index
        t_index = trade.time  # Unique ID for the row widgets

        # 1. Prevent duplicate rows if this trade is already tracked
        if t_index in self.body_widgets['symbol']:
            return

        # 2. Date Formatting
        dt_str = datetime.datetime.fromtimestamp(trade.time / 1000).strftime("%b %d %H:%M")

        # 3. Time Label
        self.body_widgets['time'][t_index] = tk.Label(self._body_frame.sub_frame, text=dt_str, bg=BG_COLOR,
                                                      fg=FG_COLOR_2, font=GLOBAL_FONT, width=self._col_width)
        self.body_widgets['time'][t_index].grid(row=b_index, column=0)

        # 4. Symbol Label
        self.body_widgets['symbol'][t_index] = tk.Label(self._body_frame.sub_frame, text=trade.symbol,
                                                        bg=BG_COLOR, fg=FG_COLOR_2, font=GLOBAL_FONT,
                                                        width=self._col_width)
        self.body_widgets['symbol'][t_index].grid(row=b_index, column=1)

        # 5. Exchange Label
        # Use getattr safely in case the contract object is missing the exchange attribute
        exchange_name = getattr(trade.contract, 'exchange', 'Unknown').capitalize()
        self.body_widgets['exchange'][t_index] = tk.Label(self._body_frame.sub_frame, text=exchange_name,
                                                          bg=BG_COLOR, fg=FG_COLOR_2, font=GLOBAL_FONT,
                                                          width=self._col_width)
        self.body_widgets['exchange'][t_index].grid(row=b_index, column=2)

        # 6. Strategy Label
        self.body_widgets['strategy'][t_index] = tk.Label(self._body_frame.sub_frame, text=trade.strategy,
                                                          bg=BG_COLOR, fg=FG_COLOR_2, font=GLOBAL_FONT,
                                                          width=self._col_width)
        self.body_widgets['strategy'][t_index].grid(row=b_index, column=3)

        # 7. Side Label
        side_color = GREEN if trade.side.lower() == "long" else RED
        self.body_widgets['side'][t_index] = tk.Label(self._body_frame.sub_frame, text=trade.side.upper(),
                                                      bg=BG_COLOR, fg=side_color, font=BOLD_FONT,
                                                      width=self._col_width)
        self.body_widgets['side'][t_index].grid(row=b_index, column=4)

        # 8. Quantity (with Variable)
        self.body_widgets['quantity_var'][t_index] = tk.StringVar(value=str(trade.quantity))
        self.body_widgets['quantity'][t_index] = tk.Label(self._body_frame.sub_frame,
                                                          textvariable=self.body_widgets['quantity_var'][t_index],
                                                          bg=BG_COLOR, fg=FG_COLOR_2, font=GLOBAL_FONT,
                                                          width=self._col_width)
        self.body_widgets['quantity'][t_index].grid(row=b_index, column=5)

        # 9. Status (with Variable)
        self.body_widgets['status_var'][t_index] = tk.StringVar(value=trade.status.capitalize())
        self.body_widgets['status'][t_index] = tk.Label(self._body_frame.sub_frame,
                                                        textvariable=self.body_widgets['status_var'][t_index],
                                                        bg=BG_COLOR, fg=FG_COLOR_2, font=GLOBAL_FONT,
                                                        width=self._col_width)
        self.body_widgets['status'][t_index].grid(row=b_index, column=6)

        # 10. PnL (with Variable)
        self.body_widgets['pnl_var'][t_index] = tk.StringVar(value=f"{trade.pnl:.2f}")
        self.body_widgets['pnl'][t_index] = tk.Label(self._body_frame.sub_frame,
                                                     textvariable=self.body_widgets['pnl_var'][t_index],
                                                     bg=BG_COLOR, fg=FG_COLOR_2, font=BOLD_FONT,
                                                     width=self._col_width)
        self.body_widgets['pnl'][t_index].grid(row=b_index, column=7)

        # 11. Action Button (Manual Close)
        # We store the button in the 'action' dict we initialized in __init__
        self.body_widgets['action'][t_index] = tk.Button(self._body_frame.sub_frame, text="X",
                                                         bg=RED, fg=FG_COLOR, font=BOLD_FONT,
                                                         command=lambda: self._close_trade(trade),
                                                         width=6)
        self.body_widgets['action'][t_index].grid(row=b_index, column=8, pady=2)

        self._body_index += 1

    def _close_trade(self, trade: Trade):
        t_index = trade.time

        # 1. Disable the button so you can't click it again
        if t_index in self.body_widgets['action']:
            self.body_widgets['action'][t_index].config(state=tk.DISABLED)

        # 2. Update the status text IMMEDIATELY
        if t_index in self.body_widgets['status_var']:
            self.body_widgets['status_var'][t_index].set("Closing...")
            self.body_widgets['status'][t_index].config(fg="orange")  # Optional: Change color to indicate progress

        # 3. Offload the slow work to the background
        import threading
        threading.Thread(target=self._execute_close, args=(trade,), daemon=True).start()

    def _execute_close(self, trade: Trade):

        """Network call logic running in background."""
        try:
            # This calls the Root method we just updated
            success = self.root.manual_close(trade)

            if success:
                # Give the user a moment to see the "Closing..." status
                time.sleep(0.5)
                # Thread-safe UI removal
                self.after(0, lambda: self.remove_trade(trade.time))
            else:
                # If the API rejected it, reset the button so the user can try again
                self.after(0, lambda: self._reset_trade_ui(trade))

        except Exception as e:
            logger.error(f"Thread Error: {e}")
            self.after(0, lambda: self._reset_trade_ui(trade))

    def _reset_trade_ui(self, trade: Trade):
        """Helper to restore the row if the close fails."""
        t_index = trade.time
        if t_index in self.body_widgets['action']:
            self.body_widgets['action'][t_index].config(state=tk.NORMAL)
            self.body_widgets['status_var'][t_index].set(trade.status.capitalize())
            self.body_widgets['status'][t_index].config(fg=FG_COLOR_2)

    def remove_trade(self, t_index: int):
        for key in self.body_widgets.keys():
            if t_index in self.body_widgets[key]:
                widget = self.body_widgets[key][t_index]

                # Only call .destroy() if it's a UI Widget (Label, Button, etc.)
                if isinstance(widget, tk.Widget):
                    widget.destroy()

                # For StringVars, we just remove the reference from the dict
                del self.body_widgets[key][t_index]

        logger.info(f"TradesWatch: Row for trade {t_index} successfully removed.")

    def update_trade_log(self, trade: Trade):
        """
        Updates the PNL and status of a trade row in real-time.
        If the trade doesn't exist yet, it adds it.
        """

        t_index = trade.time

        if t_index in self.body_widgets['pnl_var']:

            # --- 1. Update PNL Value and Color ---
            pnl_value = trade.pnl
            pnl_var = self.body_widgets['pnl_var'][t_index]
            pnl_widget = self.body_widgets['pnl'][t_index]

            if pnl_value > 0:
                color = "green"
                text = f"+{pnl_value:.2f}"
            elif pnl_value < 0:
                color = "red"
                text = f"{pnl_value:.2f}"
            else:
                color = FG_COLOR_2
                text = "0.00"

            pnl_var.set(text)
            pnl_widget.config(fg=color)

            # --- 2. Update Status ---
            status_var = self.body_widgets['status_var'][t_index]
            status_var.set(trade.status.capitalize())

            # Update Quantity (important for partial fills)
            self.body_widgets['quantity_var'][t_index].set(trade.quantity)

        else:
            # RACE CONDITION FIX: If we get an update for a trade not yet in the UI, add it now.
            logger.info(f"TradesWatch: Creating new row for untracked trade ID: {t_index}")
            self.add_trade(trade)












