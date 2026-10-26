"""Shunt main window — Backup + Restore tabs over the robocopy engine."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from . import __version__, elevate
from .core import backup as backup_core
from .core import catalog, profiles
from .core import restore as restore_core
from .ui import theme
from .ui.tooltip import Tooltip
from .ui.tree import CheckTree


def _res(name: str) -> Path:
    """Asset path that works both from source and inside PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


# ---- debug log (toggled from the header) -------------------------------
_debug_fp = None


def _set_debug(on: bool) -> Path | None:
    global _debug_fp
    if on and _debug_fp is None:
        p = Path.home() / "Desktop" / "shunt_debug.log"
        try:
            _debug_fp = open(p, "a", encoding="utf-8", buffering=1)
            _debug_fp.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            return p
        except OSError:
            _debug_fp = None
    elif not on and _debug_fp is not None:
        try:
            _debug_fp.close()
        except Exception:
            pass
        _debug_fp = None
    return None


def _dbg(line: str) -> None:
    if _debug_fp is not None:
        try:
            _debug_fp.write(line + "\n")
        except Exception:
            pass


class ShuntApp(ctk.CTk):
    def __init__(self):
        theme.apply()
        super().__init__()
        self.title(f"SHUNT v{__version__}  //  data migrator")
        self.geometry("1150x800")
        self.minsize(950, 650)
        self.configure(fg_color=theme.BG)
        self.admin = elevate.is_admin()
        try:
            self.iconbitmap(str(_res("shunt.ico")))
        except Exception:
            pass

        self._build_header()

        self.tabs = ctk.CTkTabview(
            self, fg_color=theme.BG,
            segmented_button_fg_color=theme.PANEL,
            segmented_button_selected_color=theme.GREEN_DARK,
            segmented_button_selected_hover_color=theme.GREEN_DEEP,
            segmented_button_unselected_color=theme.PANEL,
            text_color=theme.TEXT, anchor="w")
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabs.add("  BACKUP — pull from old drive  ")
        self.tabs.add("  RESTORE — import into profile  ")

        self.backup_tab = BackupPanel(self.tabs.tab("  BACKUP — pull from old drive  "),
                                      admin=self.admin)
        self.backup_tab.pack(fill="both", expand=True)
        self.restore_tab = RestorePanel(self.tabs.tab("  RESTORE — import into profile  "),
                                        admin=self.admin)
        self.restore_tab.pack(fill="both", expand=True)

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color=theme.BG, height=34)
        bar.pack(fill="x", padx=10, pady=(8, 0))
        try:
            self._logo = ctk.CTkImage(
                Image.open(_res("shunt.png")), size=(42, 42))
            ctk.CTkLabel(bar, text="S H U N T", image=self._logo,
                         compound="left", font=theme.FONT_TITLE,
                         text_color=theme.GREEN).pack(side="left")
        except Exception:
            ctk.CTkLabel(bar, text="S H U N T", font=theme.FONT_TITLE,
                         text_color=theme.GREEN).pack(side="left")
        state = ("ADMIN — backup-mode /B active" if self.admin
                 else "NOT ELEVATED — ACL bypass off")
        ctk.CTkLabel(bar, text=state, font=theme.FONT_MONO,
                     text_color=theme.GREEN if self.admin else theme.AMBER
                     ).pack(side="right")
        self.debug_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="debug log", variable=self.debug_var,
                        command=self._toggle_debug, font=theme.FONT_MONO,
                        text_color=theme.TEXT_DIM, fg_color=theme.GREEN,
                        hover_color=theme.GREEN_HOVER,
                        checkbox_height=16, checkbox_width=16,
                        corner_radius=2).pack(side="right", padx=10)

    def _toggle_debug(self) -> None:
        p = _set_debug(self.debug_var.get())
        if p:
            self.backup_tab.log(f":: debug log → {p}")
            self.restore_tab.log(f":: debug log → {p}")


class _JobPanel(ctk.CTkFrame):
    """Shared worker/queue/console plumbing for both tabs."""

    def __init__(self, master, *, admin: bool):
        super().__init__(master, fg_color=theme.BG)
        self.admin = admin
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self._item_i = self._item_n = 0
        self._item_label = ""
        self._start_label = ""

    # ---- console ----
    def log(self, msg: str) -> None:
        self._log_lines([msg])

    def _log_lines(self, lines: list[str]) -> None:
        self.console.insert("end", "\n".join(lines) + "\n")
        n = int(self.console.index("end-1c").split(".")[0])
        if n > 1500:
            self.console.delete("1.0", f"{n - 1200}.0")
        self.console.see("end")

    def _drain(self) -> None:
        # Bounded per cycle — under a robocopy flood the queue refills
        # faster than it drains; an unbounded loop starves the mainloop
        # and Windows marks the window "Not Responding".
        deadline = time.perf_counter() + 0.025
        logs: list[str] = []
        skipped = 0
        try:
            while time.perf_counter() < deadline:
                evt = self.q.get_nowait()
                if evt[0] == "log":
                    _dbg(evt[1])
                    if len(logs) < 600:
                        logs.append(evt[1])
                    else:
                        skipped += 1
                else:
                    if logs or skipped:
                        self._flush_logs(logs, skipped)
                        logs.clear()
                        skipped = 0
                    self._on_event(evt)
        except queue.Empty:
            pass
        if logs or skipped:
            self._flush_logs(logs, skipped)
        if self.running:
            self.after(20, self._drain)

    def _flush_logs(self, lines: list[str], skipped: int) -> None:
        if skipped:
            lines.append(f"… {skipped} lines skipped "
                         f"(full detail in report/debug log)")
            _dbg(lines[-1])
        self._log_lines(lines)

    def _on_event(self, evt: tuple) -> None:
        kind = evt[0]
        if kind == "log":
            self.log(evt[1])
        elif kind == "phase":
            self.log(f":: {evt[1]}")
            self.status.configure(text=evt[1])
        elif kind == "file":
            if self._item_label:
                self.status.configure(
                    text=f"[{self._item_i}/{self._item_n}] "
                         f"{self._item_label} — {evt[1]}")
            else:
                self.status.configure(text=evt[1])
        elif kind == "item_start":
            _, i, n, label = evt
            self._item_i, self._item_n, self._item_label = i, n, label
            self.progress.set((i - 1) / n)
            self.status.configure(text=f"[{i}/{n}] {label}")
        elif kind == "item_done":
            _, label, res = evt
            if self._item_n:
                self.progress.set(self._item_i / self._item_n)
            if res.cancelled:
                self.log(f"  ✕ {label} — cancelled")
            elif res.ok:
                self.log(f"  ✓ {label} — {res.files_copied} files "
                         f"(exit {res.exit_code})")
            else:
                self.log(f"  ✕ {label} — FAILED (exit {res.exit_code})")
        elif kind == "zip_progress":
            _, i, total = evt
            if total:
                self.progress.set(i / total)
            self.status.configure(text=f"compressing {i}/{total} files")
        elif kind == "done":
            self._finish(evt[1])
        else:
            self._on_panel_event(evt)

    def _on_panel_event(self, evt: tuple) -> None:
        """Hook for panel-specific event kinds (overridden by RestorePanel)."""

    def _start(self, target, kwargs: dict) -> None:
        self.cancel.clear()
        self.running = True
        self.progress.set(0)
        self._item_label = ""
        self.start_btn.configure(state="disabled", text="» RUNNING…")
        self.cancel_btn.configure(state="normal")
        threading.Thread(target=self._worker, args=(target, kwargs),
                         daemon=True).start()
        self.after(60, self._drain)

    def _worker(self, target, kwargs: dict) -> None:
        try:
            target(**kwargs)
        except InterruptedError:
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("log", f"!! worker error: {e}"))
            self.q.put(("done", None))

    def _finish(self, report: Path | None) -> None:
        self.running = False
        self.progress.set(1)
        self.start_btn.configure(state="normal", text=self._start_label)
        self.cancel_btn.configure(state="disabled")
        if report:
            self.status.configure(text="done")
            self.log(f":: report → {report}")
        else:
            self.status.configure(text="finished with errors")

    def _cancel(self) -> None:
        self.cancel.set()
        self.status.configure(text="cancelling…")

    def _build_console(self, parent) -> None:
        bottom = ctk.CTkFrame(parent, **theme.W["frame"])
        bottom.grid(row=2, column=0, sticky="nsew", padx=4, pady=(6, 0))
        bottom.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(bottom, progress_color=theme.GREEN,
                                           fg_color=theme.GREEN_DEEP)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))

        self.status = ctk.CTkLabel(bottom, text="idle", font=theme.FONT_MONO,
                                   text_color=theme.TEXT_DIM)
        self.status.grid(row=1, column=0, sticky="w", padx=8)

        self.console = ctk.CTkTextbox(bottom, fg_color="#020502",
                                      text_color=theme.GREEN,
                                      font=theme.FONT_MONO, wrap="none",
                                      border_color=theme.BORDER, border_width=1)
        self.console.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        bottom.grid_rowconfigure(2, weight=1)


class BackupPanel(_JobPanel):
    def __init__(self, master, *, admin: bool):
        super().__init__(master, admin=admin)
        self.profile_root: Path | None = None
        self._profiles: dict[str, Path] = {}
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=3)
        self.grid_rowconfigure(2, weight=1)

        # --- source row ---
        src = ctk.CTkFrame(self, **theme.W["frame"])
        src.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(src, text="SOURCE", font=theme.FONT_BOLD,
                     text_color=theme.TEXT_DIM).pack(side="left", padx=8)

        candidates = profiles.drive_candidates() or [str(Path.home().parent)]
        self.src_var = tk.StringVar(value=candidates[0])
        self.src_box = ctk.CTkComboBox(
            src, values=candidates, variable=self.src_var,
            width=340, **{k: v for k, v in theme.W["entry"].items()
                          if k != "corner_radius"})
        self.src_box.pack(side="left", padx=4, pady=8)
        self.src_box.bind("<Return>", lambda _e: self._scan())

        ctk.CTkButton(src, text="browse", width=80,
                      command=self._browse_src, **theme.W["button"]
                      ).pack(side="left", padx=4)
        ctk.CTkButton(src, text="scan", width=80,
                      command=self._scan, **theme.W["button"]
                      ).pack(side="left", padx=4)

        ctk.CTkLabel(src, text="PROFILE", font=theme.FONT_BOLD,
                     text_color=theme.TEXT_DIM).pack(side="left", padx=(18, 4))
        self.profile_menu = ctk.CTkOptionMenu(
            src, values=["—"], command=self._pick_profile,
            fg_color=theme.GREEN_DARK, button_color=theme.GREEN_DARK,
            button_hover_color=theme.GREEN_HOVER, text_color=theme.TEXT,
            font=theme.FONT)
        self.profile_menu.pack(side="left", padx=4)

        # --- middle: tree + destination ---
        mid = ctk.CTkFrame(self, fg_color=theme.BG)
        mid.grid(row=1, column=0, sticky="nsew", padx=4)
        mid.grid_columnconfigure(0, weight=3)
        mid.grid_columnconfigure(1, weight=1, minsize=300)
        mid.grid_rowconfigure(0, weight=1)

        tree_frame = ctk.CTkFrame(mid, **theme.W["frame"])
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.tree = CheckTree(tree_frame)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ctk.CTkScrollbar(tree_frame, command=self.tree.yview,
                              fg_color=theme.PANEL, button_color=theme.GREEN_DARK)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        right = ctk.CTkFrame(mid, **theme.W["frame"])
        right.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(right, text="DESTINATION", font=theme.FONT_BOLD,
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=10, pady=(10, 4))

        self.dest_mode = tk.StringVar(value="folder")
        for val, text in (("folder", "folder (drive / share)"),
                          ("zip", "zip archive")):
            ctk.CTkRadioButton(right, text=text, variable=self.dest_mode,
                               value=val, font=theme.FONT,
                               text_color=theme.TEXT, fg_color=theme.GREEN,
                               hover_color=theme.GREEN_HOVER,
                               command=self._dest_mode_changed
                               ).pack(anchor="w", padx=12, pady=2)

        self.dest_var = tk.StringVar()
        ctk.CTkEntry(right, textvariable=self.dest_var,
                     **theme.W["entry"]).pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkButton(right, text="browse…", command=self._browse_dest,
                      **theme.W["button"]).pack(padx=10, anchor="w")

        ctk.CTkLabel(
            right, font=theme.FONT_MONO, text_color=theme.TEXT_DIM,
            justify="left", anchor="w",
            text=("\n"
                  "copies: docs · pics · vids · music ·\n"
                  "desktop · downloads · favorites\n"
                  "+ selective AppData (browsers,\n"
                  "mail sigs, sticky notes)\n"
                  "\n"
                  "skips: junctions, OneDrive\n"
                  "placeholders, caches, *.ost\n"
                  "\n"
                  "note: browser passwords do NOT\n"
                  "migrate (DPAPI) — export them\n"
                  "on the old machine first.")
        ).pack(anchor="w", padx=10)

        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(side="bottom", fill="x", padx=10, pady=10)
        self._start_label = "▶ START BACKUP"
        self.start_btn = ctk.CTkButton(
            btns, text=self._start_label, font=theme.FONT_BOLD,
            fg_color=theme.GREEN_DARK, hover_color=theme.GREEN_HOVER,
            text_color="#eaffea", border_color=theme.GREEN, border_width=1,
            command=self._start_backup)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        Tooltip(self.start_btn,
                "Copy every ticked folder/AppData item from the source\n"
                "profile into the destination (robocopy /B — ACL-safe,\n"
                "multi-threaded). Writes a manifest + report.")
        self.cancel_btn = ctk.CTkButton(
            btns, text="✕", width=40, fg_color="#3a1212",
            hover_color=theme.RED, text_color="#ffd7d7",
            state="disabled", command=self._cancel)
        self.cancel_btn.pack(side="left")

        self._build_console(self)
        self._scan()

    # ---- source/profile selection ----
    def _browse_src(self) -> None:
        p = filedialog.askdirectory(title="Source: drive root, Users dir, or profile")
        if p:
            self.src_var.set(p)
            self._scan()

    def _scan(self) -> None:
        src = Path(self.src_var.get().strip().strip('"'))
        self.tree.clear()
        self._profiles.clear()
        if not src.exists():
            self.profile_menu.configure(values=["—"])
            self.profile_menu.set("—")
            return
        found = profiles.enumerate_profiles(src)
        if not found:
            self.profile_menu.configure(values=["—"])
            self.profile_menu.set("—")
            self.log(f"!! no user profiles under {src}")
            return
        self._profiles = {p.name: p for p in found}
        names = list(self._profiles)
        self.profile_menu.configure(values=names)
        self.profile_menu.set(names[0])
        self._pick_profile(names[0])

    def _pick_profile(self, name: str) -> None:
        self.profile_root = self._profiles.get(name)
        if not self.profile_root:
            return
        self.tree.clear()
        self.log(f":: profile → {self.profile_root}")
        n = 0
        for item in catalog.build(self.profile_root):
            self._add_item("", f"i{n}", item)
            n += 1
        self._suggest_dest()

    def _add_item(self, parent: str, iid: str, item) -> None:
        label = item.label + (f"  · {item.note}" if item.note else "")
        self.tree.add(parent, iid, label, checked=item.checked, payload=item,
                      open_=True)
        for j, child in enumerate(item.children):
            self._add_item(iid, f"{iid}.{j}", child)

    # ---- destination ----
    def _dest_mode_changed(self) -> None:
        self._suggest_dest(force=True)

    def _suggest_dest(self, force: bool = False) -> None:
        if not self.profile_root:
            return
        stamp = datetime.now().strftime("%Y%m%d")
        desktop = Path.home() / "Desktop"
        if self.dest_mode.get() == "zip":
            suggestion = desktop / f"ShuntBackup_{self.profile_root.name}_{stamp}.zip"
        else:
            suggestion = desktop / f"ShuntBackup_{self.profile_root.name}_{stamp}"
        if force or not self.dest_var.get():
            self.dest_var.set(str(suggestion))

    def _browse_dest(self) -> None:
        if self.dest_mode.get() == "zip":
            p = filedialog.asksaveasfilename(
                title="Backup zip", defaultextension=".zip",
                filetypes=[("Zip archive", "*.zip")])
        else:
            p = filedialog.askdirectory(title="Backup destination folder")
        if p:
            self.dest_var.set(p)

    # ---- run ----
    def _start_backup(self) -> None:
        if not self.profile_root:
            messagebox.showerror("Shunt", "Scan a source and pick a profile first.")
            return
        items = self.tree.get_checked()
        if not items:
            messagebox.showerror("Shunt", "Nothing ticked.")
            return
        dest = Path(self.dest_var.get().strip().strip('"'))
        if not str(dest):
            return
        mode = self.dest_mode.get()
        self.log(f":: backup {self.profile_root} → {dest} [{mode}]")
        self._start(backup_core.run_backup,
                    dict(profile_root=self.profile_root, items=items,
                         dest=dest, mode=mode, admin=self.admin,
                         on_event=self.q.put, cancel=self.cancel))


class RestorePanel(_JobPanel):
    def __init__(self, master, *, admin: bool):
        super().__init__(master, admin=admin)
        self._backup: Path | None = None
        self._kind: str | None = None
        self._build()

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=3)
        self.grid_rowconfigure(2, weight=1)

        src = ctk.CTkFrame(self, **theme.W["frame"])
        src.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(src, text="BACKUP", font=theme.FONT_BOLD,
                     text_color=theme.TEXT_DIM).pack(side="left", padx=8)
        self.src_var = tk.StringVar()
        e = ctk.CTkEntry(src, textvariable=self.src_var, width=380,
                         **theme.W["entry"])
        e.pack(side="left", padx=4, pady=8)
        e.bind("<Return>", lambda _e: self._inspect())
        ctk.CTkButton(src, text="folder…", width=90,
                      command=lambda: self._browse("folder"),
                      **theme.W["button"]).pack(side="left", padx=4)
        ctk.CTkButton(src, text="zip…", width=70,
                      command=lambda: self._browse("zip"),
                      **theme.W["button"]).pack(side="left", padx=4)
        ctk.CTkButton(src, text="inspect", width=80,
                      command=self._inspect, **theme.W["button"]
                      ).pack(side="left", padx=4)
        self.info_lbl = ctk.CTkLabel(src, text="", font=theme.FONT_MONO,
                                     text_color=theme.TEXT_DIM)
        self.info_lbl.pack(side="left", padx=12)

        mid = ctk.CTkFrame(self, fg_color=theme.BG)
        mid.grid(row=1, column=0, sticky="nsew", padx=4)
        mid.grid_columnconfigure(0, weight=3)
        mid.grid_columnconfigure(1, weight=1, minsize=300)
        mid.grid_rowconfigure(0, weight=1)

        tree_frame = ctk.CTkFrame(mid, **theme.W["frame"])
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.tree = CheckTree(tree_frame)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ctk.CTkScrollbar(tree_frame, command=self.tree.yview,
                              fg_color=theme.PANEL, button_color=theme.GREEN_DARK)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        right = ctk.CTkFrame(mid, **theme.W["frame"])
        right.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(right, text="RESTORE INTO", font=theme.FONT_BOLD,
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=10, pady=(10, 4))
        self.dest_var = tk.StringVar(value=os.environ.get("USERPROFILE", ""))
        ctk.CTkEntry(right, textvariable=self.dest_var,
                     **theme.W["entry"]).pack(fill="x", padx=10, pady=4)
        ctk.CTkButton(right, text="browse…", command=self._browse_dest,
                      **theme.W["button"]).pack(padx=10, anchor="w")
        ctk.CTkLabel(
            right, font=theme.FONT_MONO, text_color=theme.TEXT_DIM,
            justify="left", anchor="w",
            text=("\npaths in the backup are\n"
                  "profile-relative, so they\n"
                  "land correctly even if the\n"
                  "new username differs.\n"
                  "\n"
                  "close browsers & outlook on\n"
                  "this machine before restoring\n"
                  "into a live profile.")
        ).pack(anchor="w", padx=10)

        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(side="bottom", fill="x", padx=10, pady=10)
        self._start_label = "▶ START RESTORE"
        self.start_btn = ctk.CTkButton(
            btns, text=self._start_label, font=theme.FONT_BOLD,
            fg_color=theme.GREEN_DARK, hover_color=theme.GREEN_HOVER,
            text_color="#eaffea", border_color=theme.GREEN, border_width=1,
            command=self._start_restore)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        Tooltip(self.start_btn,
                "Copy the ticked items from this backup into the target\n"
                "profile. Paths are profile-relative, so a different\n"
                "username is fine. Close browsers/Outlook first.")
        self.cancel_btn = ctk.CTkButton(
            btns, text="✕", width=40, fg_color="#3a1212",
            hover_color=theme.RED, text_color="#ffd7d7",
            state="disabled", command=self._cancel)
        self.cancel_btn.pack(side="left")

        self._build_console(self)

    # ---- backup inspection ----
    def _browse(self, kind: str) -> None:
        if kind == "zip":
            p = filedialog.askopenfilename(
                title="Backup zip", filetypes=[("Zip archive", "*.zip")])
        else:
            p = filedialog.askdirectory(title="Backup folder")
        if p:
            self.src_var.set(p)
            self._inspect()

    def _inspect(self) -> None:
        p = Path(self.src_var.get().strip().strip('"'))
        self.tree.clear()
        if not p.exists():
            self.info_lbl.configure(text="not found")
            return
        # zips with many entries take seconds to list — keep UI alive
        self._backup = None
        self.info_lbl.configure(text="inspecting…")
        self.running = True
        threading.Thread(target=self._inspect_worker, args=(p,),
                         daemon=True).start()
        self.after(20, self._drain)

    def _inspect_worker(self, p: Path) -> None:
        try:
            self.q.put(("inspect", p, restore_core.inspect_backup(p)))
        except Exception as e:
            self.q.put(("inspect_err", str(e)))

    def _on_panel_event(self, evt: tuple) -> None:
        self.running = False
        if evt[0] == "inspect_err":
            self.info_lbl.configure(text=f"error: {evt[1]}")
            return
        _, p, info = evt
        self._backup, self._kind = p, info["kind"]
        m = info["manifest"]
        if m:
            self.info_lbl.configure(
                text=f"manifest ✓  src={m.get('source_profile')}  "
                     f"{m.get('created', '')[:10]}")
        else:
            self.info_lbl.configure(
                text=f"no manifest — {info['kind']} top-level dirs")
        for i, rel in enumerate(info["items"]):
            self.tree.add("", f"r{i}", rel, checked=True, payload=rel)

    def _browse_dest(self) -> None:
        p = filedialog.askdirectory(title="Target profile folder")
        if p:
            self.dest_var.set(p)

    # ---- run ----
    def _start_restore(self) -> None:
        if not self._backup:
            messagebox.showerror("Shunt", "Pick and inspect a backup first.")
            return
        items = self.tree.get_checked()
        if not items:
            messagebox.showerror("Shunt", "Nothing ticked.")
            return
        dest = Path(self.dest_var.get().strip().strip('"'))
        self.log(f":: restore {self._backup} → {dest} [{self._kind}]")
        self._start(restore_core.run_restore,
                    dict(backup=self._backup, kind=self._kind,
                         rel_items=[str(i) for i in items],
                         dest_profile=dest, admin=self.admin,
                         on_event=self.q.put, cancel=self.cancel))


def main() -> None:
    if not elevate.is_admin():
        if messagebox.askyesno(
                "Shunt",
                "Run as administrator?\n\nRecommended — enables robocopy "
                "backup-mode (/B) so ACL checks are bypassed entirely."):
            elevate.relaunch_elevated()
            return
    app = ShuntApp()
    app.mainloop()


if __name__ == "__main__":
    main()
