"""Checkable ttk.Treeview — [ ]/[x]/[-] glyphs, fits the terminal theme.

Payloads: arbitrary per-node object (e.g. CatalogItem). get_checked()
returns payloads of checked nodes that have no checked ancestor (so a
ticked parent isn't double-counted alongside its ticked children).
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk

from . import theme


class CheckTree(ttk.Treeview):
    _GLYPH = {False: "[ ]", True: "[x]", None: "[-]"}

    def __init__(self, master, **kw):
        self._setup_style()
        super().__init__(master, show="tree", **kw)
        self._labels: dict[str, str] = {}
        self._state: dict[str, bool] = {}
        self._payload: dict[str, object] = {}
        self.bind("<Button-1>", self._on_click)

    @staticmethod
    def _setup_style() -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview", background=theme.PANEL, fieldbackground=theme.PANEL,
            foreground=theme.GREEN, font=theme.FONT_MONO, rowheight=24,
            borderwidth=0)
        style.map("Treeview",
                  background=[("selected", theme.GREEN_DEEP)],
                  foreground=[("selected", "#eaffea")])

    def add(self, parent: str, iid: str, label: str, *,
            checked: bool = True, payload=None, open_: bool = True) -> None:
        self._labels[iid] = label
        self._state[iid] = checked
        if payload is not None:
            self._payload[iid] = payload
        self.insert(parent, "end", iid=iid,
                    text=f"{self._GLYPH[checked]} {label}", open=open_)
        self._fix_ancestors(iid)

    def toggle(self, iid: str) -> None:
        new = not self._state.get(iid, False)
        self._set_subtree(iid, new)
        self._fix_ancestors(iid)

    def _set_subtree(self, iid: str, val: bool) -> None:
        self._state[iid] = val
        self._refresh(iid)
        for child in self.get_children(iid):
            self._set_subtree(child, val)

    def _fix_ancestors(self, iid: str) -> None:
        parent = self.parent(iid)
        while parent:
            kids = self.get_children(parent)
            states = [self._state[c] for c in kids]
            self._state[parent] = (all(states) if all(s is not None for s in states)
                                   else (None if any(s for s in states) else False))
            self._refresh(parent)
            parent = self.parent(parent)

    def _refresh(self, iid: str) -> None:
        self.item(iid, text=f"{self._GLYPH[self._state[iid]]} {self._labels[iid]}")

    def _on_click(self, event) -> str | None:
        iid = self.identify_row(event.y)
        if not iid:
            return None
        bbox = self.bbox(iid)
        if bbox and event.x <= bbox[0] + 30:
            self.toggle(iid)
            return "break"
        return None

    def clear(self) -> None:
        self.delete(*self.get_children())
        self._labels.clear()
        self._state.clear()
        self._payload.clear()

    def get_checked(self) -> list:
        """Payloads of checked nodes with no checked ancestor."""
        out = []
        for iid, payload in self._payload.items():
            if self._state.get(iid) is True and not self._has_checked_ancestor(iid):
                out.append(payload)
        return out

    def _has_checked_ancestor(self, iid: str) -> bool:
        parent = self.parent(iid)
        while parent:
            if self._state.get(parent) is True and parent in self._payload:
                return True
            parent = self.parent(parent)
        return False
