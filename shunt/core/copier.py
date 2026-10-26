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

import re
import subprocess
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


@dataclass
class ItemResult:
    rel_path: str
    exit_code: int = -1
    files_copied: int = 0
    files_failed: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        # robocopy exit codes are bitflags; >=8 means real failures
        return 0 <= self.exit_code < 8


def build_args(src: Path, dst: Path, *, admin: bool, overwrite: bool = False,
               extra_xd: tuple = (), extra_xf: tuple = ()) -> list[str]:
    args = ["robocopy", str(src), str(dst), "/E", "/COPY:DAT", "/DCOPY:T",
            "/R:1", "/W:1", "/XJ", "/XA:O", "/MT:16", "/NP"]
    if admin:
        args.append("/B")
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
             extra_xd: tuple = (), extra_xf: tuple = (),
             on_line: Callable[[str], None] | None = None,
             on_file: Callable[[str], None] | None = None,
             cancel: Event | None = None) -> ItemResult:
    res = ItemResult(rel_path=rel_path)

    if not src.exists():
        res.exit_code = 0
        return res

    args = build_args(src, dst, admin=admin, overwrite=overwrite,
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

    last_file_emit = 0.0
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
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
    return res
