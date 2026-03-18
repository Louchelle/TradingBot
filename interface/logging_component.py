import tkinter as tk
import logging
from interface.styling import *


class Logging(tk.Frame, logging.Handler):
    def __init__(self, parent, *args, **kwargs):
        tk.Frame.__init__(self, parent, *args, **kwargs)
        logging.Handler.__init__(self)

        self.logging_text = tk.Text(self, height=10, width=60, state=tk.DISABLED,
                                    bg=BG_COLOR, fg=FG_COLOR_2, font=GLOBAL_FONT,
                                    highlightthickness=False, bd=0)
        self.logging_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.setLevel(logging.INFO)
        logging.getLogger().addHandler(self)

    def emit(self, record):
        message = self.format(record)

        # 1. Filter high-frequency noise
        noise = ["pnl", "price", "ticker", "ping", "pong", "update"]
        if any(word in message.lower() for word in noise):
            if "heartbeat" not in message.lower() and "hb:" not in message.lower():
                return

        # 2. Jump to the Main Thread immediately
        try:
            self.after(0, self.add_log, message)
        except (RuntimeError, tk.TclError):
            pass

    def add_log(self, message: str):
        """Internal method: Must be called via after() if coming from a thread."""
        if not self.logging_text.winfo_exists():
            return

        try:
            self.logging_text.configure(state='normal')
            self.logging_text.insert("end", message + '\n')

            # Keep buffer capped at 500 lines
            line_count = int(self.logging_text.index('end-1c').split('.')[0])
            if line_count > 500:
                self.logging_text.delete("1.0", "2.0")

            self.logging_text.see("end")
            self.logging_text.configure(state='disabled')
        except Exception as e:
            # Silent fail to prevent console spam during shutdown
            pass