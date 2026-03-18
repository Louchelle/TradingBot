import tkinter as tk
from interface.styling import *


class Autocomplete(tk.Entry):
    def __init__(self, symbols, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self._symbols = symbols
        self._listbox = None
        self._listbox_height = 5

        # Bindings
        self.bind("<KeyRelease>", self._update_suggestions)
        self.bind("<Down>", self._on_arrow_down)
        self.bind("<Up>", self._on_arrow_up)
        self.bind("<Return>", self._on_enter, add="+")  # add="+" keeps existing bindings in watchlist

    def _update_suggestions(self, event):
        value = self.get().strip().upper()

        # If backspace makes the field empty, kill the listbox
        if value == "":
            self._close_suggestions()
            return

        # Filter symbols
        data = [s for s in self._symbols if value in s.upper()]

        if data:
            if not self._listbox:
                # Create listbox attached to the same master as the Entry
                self._listbox = tk.Listbox(self.master, bg=BG_COLOR_2, fg=FG_COLOR,
                                           exportselection=False, insertbackground=FG_COLOR,
                                           highlightthickness=0, bd=0)

                # Placement logic to make it "Stick"
                x = self.winfo_x()
                y = self.winfo_y() + self.winfo_height()
                self._listbox.place(x=x, y=y, width=self.winfo_width())
                self._listbox.lift()

            self._listbox.delete(0, tk.END)
            for item in data:
                self._listbox.insert(tk.END, item)
        else:
            self._close_suggestions()

    def _on_arrow_down(self, event):
        if self._listbox:
            self._listbox.focus_set()
            self._listbox.selection_set(0)

    def _on_arrow_up(self, event):
        if self._listbox:
            self._listbox.focus_set()
            self._listbox.selection_set(tk.END)

    def _on_enter(self, event):
        """Allows selecting the highlighted listbox item with Enter key"""
        if self._listbox and self._listbox.curselection():
            index = self._listbox.curselection()[0]
            selected_symbol = self._listbox.get(index)

            self.delete(0, tk.END)
            self.insert(0, selected_symbol)
            self._close_suggestions()

            # Refocus the entry so the Watchlist Return binding can trigger
            self.focus_set()

    def _close_suggestions(self):
        if self._listbox:
            self._listbox.destroy()
            self._listbox = None