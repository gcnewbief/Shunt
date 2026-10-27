"""Robocopy engine — the reason this tool doesn't take an eternity.

/B  = backup mode: reads/writes via SeBackupPrivilege/SeRestorePrivilege,
      bypassing per-file ACL checks entirely (requires admin).
/XJ = skip junction points (kills 'My Music' loops + OneDrive junctions).
/XA:O = skip OneDrive on-demand placeholder files.
/COPY:DAT = deliberately NOT copying ACLs — old-drive SIDs are meaningless
      on the new machine; files inherit the destination profile's perms.
/R:1 /W:1 = one retry, 1s wait — locked files fail fast into the report.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Callable

CREATE_NO_WINDOW = 0x08000000

GLOBAL_XF = ("ntuser.dat*", "NTUSER.DAT*", "UsrClass.dat*", "*.ost")
GLOBAL_XD = ("$RECYCLE.BIN", "System Volume Information", "Recovery",
             "Config.Msi")

_ERR_RE = re.compile(r"ERROR\s+(\d+)\s+\(0x[0-9A-Fa-f]+\)\s+(.+)")
_SUM_RE = re.compile(r"^\s*(Dirs|Files)\s*:\s*(.+)$")
_NAME_RE = re.compile(r"\t([^\t]+)$")


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


def _proc_read_bytes(pid: int) -> int | None:
    """Total bytes read by a process — None if we can't query it.

    Used for freeze detection: a hung drive shows a frozen counter,
    while a slow-but-alive copy keeps advancing it.
    """
    h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)  # QUERY_INFORMATION
    if not h:
        return None
    c = _IO_COUNTERS()
    ok = ctypes.windll.kernel32.GetProcessIoCounters(h, ctypes.byref(c))
    ctypes.windll.kernel32.CloseHandle(h)
    return c.ReadTransferCount if ok else None


@dataclass
class ItemResult:
    rel_path: str
    exit_code: int = -1
    files_copied: int = 0
    files_failed: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    stalled: bool = False    # killed by the freeze watchdog

    @property
    def ok(self) -> bool:
        # robocopy exit codes are bitflags; >=8 means real failures
        return 0 <= self.exit_code < 8


def build_args(src: Path, dst: Path, *, admin: bool, overwrite: bool = False,
               mt: int = 16, ipg: int = 0, zb: bool = False,
               extra_xd: tuple = (), extra_xf: tuple = ()) -> list[str]:
    args = ["robocopy", str(src), str(dst), "/E", "/COPY:DAT", "/DCOPY:T",
            "/R:1", "/W:1", "/XJ", "/XA:O", "/NP"]
    if mt > 1:
        args.append(f"/MT:{mt}")
    if ipg > 0:
        args.append(f"/IPG:{ipg}")
    if admin:
        args.append("/ZB" if zb else "/B")  # /ZB = restartable, /B fallback
    elif zb:
        args.append("/Z")
    if overwrite:
        args.append("/IS")   # include "same" files — force re-copy
    xf = sorted(set(GLOBAL_XF) | set(extra_xf))
    xd = sorted(set(GLOBAL_XD) | set(extra_xd))
    if xf:
        args += ["/XF", *xf]
    if xd:
        args += ["/XD", *xd]
    return args


def run_item(src: Path, dst: Path, rel_path: str, *, admin: bool,
             overwrite: bool = False,
             mt: int = 16, ipg: int = 0, zb: bool = False,
             stall_after: int = 0,
             extra_xd: tuple = (), extra_xf: tuple = (),
             on_line: Callable[[str], None] | None = None,
             on_file: Callable[[str], None] | None = None,
             cancel: Event | None = None) -> ItemResult:
    res = ItemResult(rel_path=rel_path)

    if not src.exists():
        res.exit_code = 0
        return res

    args = build_args(src, dst, admin=admin, overwrite=overwrite,
                      mt=mt, ipg=ipg, zb=zb,
                      extra_xd=extra_xd, extra_xf=extra_xf)
    if on_line:
        on_line("> " + " ".join(args))

    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="mbcs", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        res.errors.append(f"failed to launch robocopy: {e}")
        res.exit_code = 16
        return res

    # freeze watchdog: drive is "dead" only if BOTH output and the
    # process's I/O byte counter stop advancing — a slow large file
    # produces no output lines but keeps incrementing I/O
    state = {"t": time.monotonic(), "io": _proc_read_bytes(proc.pid),
             "warned": False}

    def _touch() -> None:
        state["t"] = time.monotonic()

    if stall_after > 0:
        def _watchdog() -> None:
            while proc.poll() is None:
                time.sleep(5)
                io = _proc_read_bytes(proc.pid)
                if io is not None and io != state["io"]:
                    state["io"] = io
                    _touch()
                idle = time.monotonic() - state["t"]
                if not state["warned"] and idle > stall_after * 0.6:
                    state["warned"] = True
                    if on_line:
                        on_line(f"!! no I/O for {int(idle)}s — "
                                "drive may be frozen")
                if idle > stall_after:
                    res.stalled = True
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    return
        threading.Thread(target=_watchdog, daemon=True).start()

    last_file_emit = 0.0
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            _touch()
            if cancel is not None and cancel.is_set():
                proc.kill()
                res.cancelled = True
                break
            m = _ERR_RE.search(line)
            if m:
                name = _NAME_RE.search(line)
                res.errors.append(
                    f"ERROR {m.group(1)} {name.group(1) if name else m.group(2).strip()}")
            s = _SUM_RE.match(line)
            if s:
                nums = re.findall(r"\d+", s.group(2))
                # columns: Total Copied Skipped Mismatch FAILED Extras
                if len(nums) >= 5:
                    if s.group(1) == "Files":
                        res.files_copied = int(nums[1])
                        res.files_failed = int(nums[4])
                    elif s.group(1) == "Dirs":
                        res.dirs_copied = int(nums[1])
            # throttled — status display only, no need for per-file fidelity
            if on_file and "\t" in line:
                now = time.monotonic()
                if now - last_file_emit >= 0.1:
                    nm = _NAME_RE.search(line)
                    if nm:
                        on_file(nm.group(1).strip())
                        last_file_emit = now
            if on_line:
                on_line(line)
    finally:
        proc.wait()

    if not res.cancelled:
        res.exit_code = proc.returncode
    else:
        res.stalled = False
    return res
