r"""Nuker — force-delete a directory tree with escalating techniques.

Python port of tools/kill-staging.ps1, plus a count-then-delete pass so
the GUI can show real progress on multi-million-file trees.

Escalation:
  1. relocate to <drive>\_shunt_del (same-volume relink — instant, and
     every child path gets shorter, dodging MAX_PATH on the way out)
  2. scan pass: count entries + strip RO/hidden/system attributes
  3. python deleter on \\?\ extended paths — post-order, counts as it
     goes. Reparse points are unlinked, NEVER descended (a junction in
     the tree may point outside it)
  4. takeown + icacls (admin only), then deleter again
  5. robocopy empty-mirror — native long-path delete
  6. rd /s /q on the \\?\ path, then the 8.3 short name
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path
from threading import Event
from typing import Callable

from .copier import CREATE_NO_WINDOW

Event_ = Callable[[tuple], None]

_REPARSE = 0x400
_ATTR_BAD = 0x1 | 0x2 | 0x4          # readonly | hidden | system
_ATTR_NORMAL = 0x80

_KERNEL = ctypes.windll.kernel32


def _emit(on_event: Event_ | None, evt: tuple) -> None:
    if on_event:
        on_event(evt)


def _ext(p: Path) -> str:
    r"""\\?\ extended path — resolve() expands 8.3 names and prefixes."""
    r = str(p.resolve())
    return r if r.startswith("\\\\?\\") else "\\\\?\\" + r


def _attrs(path: str) -> int:
    return _KERNEL.GetFileAttributesW(path)


def _clear_attrs(path: str) -> None:
    a = _attrs(path)
    if a != -1 and a & _ATTR_BAD:
        _KERNEL.SetFileAttributesW(path, _ATTR_NORMAL)


# --- target safety ----------------------------------------------------
# Names only ever seen at a drive root / OS root — nuking one of these is
# always a mistake, not a job.
_NEVER = frozenset({
    "windows", "program files", "program files (x86)", "programdata",
    "users", "documents and settings", "system volume information",
    "$recycle.bin", "recovery", "perflogs", "boot", "intel", "inetpub",
})


def refuse_reason(path: Path) -> str | None:
    """Return why we won't delete `path`, or None if it's fair game."""
    r = path.resolve()
    if r.parent == r:
        return f"{r} is a drive root — pick a folder inside it"
    if r.parent == Path(r.anchor) and r.name.lower() in _NEVER:
        return f"{r} is a system root directory — refused"
    if str(r).lower() == str(Path(os.environ.get("USERPROFILE", "~"))
                             .resolve()).lower():
        return f"{r} is the profile you're logged into — refused"
    return None


def _scan(root_ext: str, on_event: Event_ | None,
          cancel: Event | None) -> tuple[int, list[str]]:
    """Count entries and strip RO/hidden/system attrs. Returns
    (total, errors). ~one pass over the tree, scandir-speed."""
    total = 0
    errors: list[str] = []
    stack = [root_ext]
    while stack:
        if cancel is not None and cancel.is_set():
            return total, errors
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            errors.append(f"scan {d}: {e}")
            continue
        for e in entries:
            total += 1
            _clear_attrs(e.path)
            try:
                st = e.stat(follow_symlinks=False)
                if (e.is_dir(follow_symlinks=False)
                        and not (st.st_file_attributes & _REPARSE)):
                    stack.append(e.path)
            except OSError as ex:
                errors.append(f"stat {e.path}: {ex}")
            if total % 2500 == 0:
                _emit(on_event, ("nuke_count", total))
    _emit(on_event, ("nuke_count", total))
    return total, errors


def _delete(root_ext: str, total: int, on_event: Event_ | None,
            cancel: Event | None) -> tuple[int, list[str]]:
    """Post-order delete under root_ext. Returns (deleted, errors)."""
    done = 0
    errors: list[str] = []
    stack: list[tuple[str, bool]] = [(root_ext, False)]
    while stack:
        if cancel is not None and cancel.is_set():
            break
        path, expanded = stack.pop()
        if expanded:
            try:
                os.rmdir(path)
            except OSError as e:
                errors.append(f"rmdir {path}: {e}")
            done += 1
        else:
            try:
                entries = list(os.scandir(path))
            except OSError as e:
                errors.append(f"scan {path}: {e}")
                done += 1
                continue
            stack.append((path, True))
            for e in entries:
                try:
                    st = e.stat(follow_symlinks=False)
                    reparse = bool(st.st_file_attributes & _REPARSE)
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    reparse, is_dir = False, False
                if is_dir and not reparse:
                    stack.append((e.path, False))
                    continue
                try:
                    if is_dir or reparse:
                        os.rmdir(e.path)     # kills the link, not target
                    else:
                        os.unlink(e.path)
                except OSError:
                    try:
                        os.unlink(e.path)    # file-type reparse point
                    except OSError as ex:
                        errors.append(f"del {e.path}: {ex}")
                done += 1
        if done % 200 == 0:
            _emit(on_event, ("nuke_progress", done, total))
            _emit(on_event, ("file", path))
    _emit(on_event, ("nuke_progress", done, total))
    return done, errors


def _short_path(path: str) -> str | None:
    buf = ctypes.create_unicode_buffer(32768)
    n = _KERNEL.GetShortPathNameW(path, buf, 32768)
    return buf.value if n else None


def _robocopy_mirror(target: Path, on_event: Event_ | None) -> None:
    empty = target.parent / "_shunt_empty"
    empty.mkdir(exist_ok=True)
    proc = subprocess.Popen(
        ["robocopy", str(empty), str(target), "/MIR",
         "/NS", "/NC", "/NJH", "/NJS", "/R:0", "/W:0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="mbcs", errors="replace", bufsize=1,
        creationflags=CREATE_NO_WINDOW)
    extra = 0
    try:
        for raw in proc.stdout:
            if "*EXTRA" in raw:
                extra += 1
                if extra % 200 == 0:
                    _emit(on_event, ("file", f"robocopy purged {extra}"))
    finally:
        proc.wait()
    _emit(on_event, ("log", f"   robocopy mirror purged {extra} entries"))
    try:
        os.rmdir(_ext(empty))
    except OSError:
        pass


def nuke_dir(path: Path, *, admin: bool,
             relocate: bool = True,
             on_event: Event_ | None = None,
             cancel: Event | None = None) -> tuple[Path, bool]:
    """Delete `path` by any means necessary. Returns (report, success)."""
    log = on_event
    target = path.resolve()
    _emit(log, ("phase", f"nuke {target}"))

    if relocate:
        short = Path(target.anchor) / "_shunt_del"
        try:
            os.rename(_ext(target), _ext(short))
            _emit(log, ("log", f"relocated -> {short}"))
            target = short
        except OSError:
            _emit(log, ("log", "relocate failed — deleting in place"))

    _emit(log, ("phase", "scanning (count + attribute strip)"))
    total, scan_errs = _scan(_ext(target), log, cancel)
    _emit(log, ("log", f"{total:,} entries to delete"))
    for e in scan_errs[:20]:
        _emit(log, ("log", f"   ! {e}"))

    _emit(log, ("phase", f"deleting {total:,} entries"))
    done, errs = _delete(_ext(target), total, log, cancel)
    _emit(log, ("log", f"python pass: {done:,} gone, {len(errs)} errors"))

    if errs and admin and not (cancel and cancel.is_set()):
        _emit(log, ("phase", "takeown + icacls, then retry"))
        subprocess.run(["takeown", "/f", str(target), "/r", "/d", "y"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        subprocess.run(["icacls", str(target), "/grant",
                        "*S-1-5-32-544:F", "/t", "/c", "/q"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        _, errs = _delete(_ext(target), total, log, cancel)
        _emit(log, ("log", f"retry: {len(errs)} errors remain"))

    if target.exists() and not (cancel and cancel.is_set()):
        _emit(log, ("phase", "robocopy empty-mirror"))
        _robocopy_mirror(target, log)
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(target)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)

    if target.exists():
        _emit(log, ("phase", "last resort: \\\\?\\ rd + short name"))
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", _ext(target)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        sn = _short_path(str(target))
        if sn and sn != str(target):
            _emit(log, ("log", f"   8.3 name: {sn}"))
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", sn],
                           capture_output=True,
                           creationflags=CREATE_NO_WINDOW)

    success = not target.exists()
    report = _report(path, target, total, done, errs, success)
    _emit(log, ("log", f"{'DELETED' if success else 'SURVIVED'} — {target}"))
    _emit(log, ("done", report))
    return report, success


def _report(orig: Path, target: Path, total: int, done: int,
            errs: list[str], success: bool) -> Path:
    lines = [
        "SHUNT nuke report",
        f"target   : {orig}",
        f"entries  : {total:,}",
        f"deleted  : {done:,}",
        f"result   : {'deleted' if success else f'SURVIVED at {target}'}",
        "",
        *["    ! " + e for e in errs[:200]],
    ]
    if not success:
        lines += ["",
                  "still there? -> open handle (reboot and retry before "
                  "launching anything) or NTFS damage (chkdsk /f)."]
    p = Path.home() / "Desktop" / "shunt_nuke_report.txt"
    try:
        p.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    return p
