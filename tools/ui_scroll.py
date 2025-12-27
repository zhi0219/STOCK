from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class VerticalScrolledFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, *args: object, **kwargs: object) -> None:
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.interior = ttk.Frame(self.canvas)
        self._interior_id = self.canvas.create_window((0, 0), window=self.interior, anchor="nw")
        self._mousewheel_bound = False

        self.interior.bind("<Configure>", self._on_interior_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.interior.bind("<Enter>", self._bind_mousewheel)
        self.interior.bind("<Leave>", self._unbind_mousewheel)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_interior_configure(self, event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._interior_id, width=event.width)

    def _bind_mousewheel(self, event: tk.Event) -> None:
        if self._mousewheel_bound:
            return
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Button-5>", self._on_mousewheel, add="+")
        self._mousewheel_bound = True

    def _unbind_mousewheel(self, event: tk.Event) -> None:
        if not self._mousewheel_bound:
            return
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")
        self._mousewheel_bound = False

    def _on_mousewheel(self, event: tk.Event) -> None:
        if not self._should_handle_event(event):
            return
        if self._is_scroll_inert():
            return
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
            return
        if getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")
            return
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return
        direction = -1 if delta > 0 else 1
        steps = max(1, int(abs(delta) / 120))
        self.canvas.yview_scroll(direction * steps, "units")

    def _should_handle_event(self, event: tk.Event) -> bool:
        widget = getattr(event, "widget", None)
        if widget is None:
            return False
        return self._is_descendant(widget)

    def _is_descendant(self, widget: tk.Misc) -> bool:
        current = widget
        while current is not None:
            if current is self.interior:
                return True
            current = current.master  # type: ignore[assignment]
        return False

    def _is_scroll_inert(self) -> bool:
        scrollregion = self.canvas.bbox("all")
        if not scrollregion:
            return True
        content_height = scrollregion[3] - scrollregion[1]
        return content_height <= self.canvas.winfo_height()
