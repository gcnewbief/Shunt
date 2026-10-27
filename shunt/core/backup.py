"""Backup orchestration: profile root -> folder or zip, with manifest+report.

Zip mode stages via robocopy first so /B still bypasses ACLs on attached
drives, then compresses the staging dir.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from .catalog import CatalogItem
from .copier import CREATE_NO_WINDOW, ItemResult, run_item
from .disk import reset_drive
from .manifest import REPORT_NAME, build_manifest, write_manifest

Event_ = Callable[[tuple], None]

# robocopy flag presets — see copier.build_args
SPEED_PRESETS = {
    "full":     dict(mt=16, ipg=0,  zb=False),
    "gentle":   dict(mt=1,  ipg=10, zb=False),
    "recovery": dict(mt=1,  ipg=50, zb=True),   # /ZB resumes mid-file
}
MAX_RESETS = 5          # drive resets attempted per item
STALL_AFTER = 180       # seconds of zero I/O before a copy is "frozen"


def _emit(on_event: Event_ | None, evt: tuple) -> None:
    if on_event:
        on_event(evt)


def _extended(p: Path) -> str:
    """Canonical \\\\?\\ extended path — resolve() expands 8.3 short
    names (which \\\\?\\ paths reject) and adds the prefix itself."""
    r = str(p.resolve())
    return r if r.startswith("\\\\?\\") else "\\\\?\\" + r


def _nuke_dir(path: Path, on_event: Event_ | None = None) -> None:
    """Delete a dir tree that may contain >260-char paths or odd names.

    Staged browser profiles get deep enough to defeat Explorer/rmdir —
    the \\\\?\\ prefix lifts the MAX_PATH limit, and if anything still
    resists, robocopy mirrors an empty dir over it (robocopy handles
    long paths natively).
    """
    raw = _extended(path)
    try:
        shutil.rmtree(raw)
    except OSError:
        pass
    if not path.exists():
        return
    empty = path.with_name(path.name + ".empty")
    try:
        empty.mkdir(exist_ok=True)
        subprocess.run(["robocopy", str(empty), str(path), "/MIR",
                        "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        shutil.rmtree(empty, ignore_errors=True)
        shutil.rmtree(raw, ignore_errors=True)
    except OSError:
        pass
    if path.exists():
        _emit(on_event, ("log", f'!! couldn\'t remove {path} — manual '
                                f'delete: rd /s /q "{raw}"'))


_FILE_ATTRIBUTE_OFFLINE = 0x1000
_DOS_MIN = (1980, 1, 1, 0, 0, 0)
_DOS_MAX = (2107, 12, 31, 23, 59, 58)


def _safe_dt(mtime: float) -> tuple[tuple, bool]:
    """DOS-range zip timestamp, and whether it was clamped — old drives
    carry corrupt mtimes (year 2408 etc.) that overflow the 16-bit DOS
    date field."""
    try:
        dt = datetime.fromtimestamp(mtime)
    except (OverflowError, OSError, ValueError):
        return _DOS_MIN, True
    if dt.year < 1980:
        return _DOS_MIN, True
    if dt.year > 2107:
        return _DOS_MAX, True
    return dt.timetuple()[:6], False


def _zip_dir(src_dir: Path, zip_path: Path,
             on_event: Event_ | None, cancel: Event | None) -> list[str]:
    """Compress staging dir into a zip. Returns per-file error strings.

    Staged files can still be unhydrated OneDrive placeholders (robocopy
    /B copies the reparse stub across) — opening them raises
    ERROR_CANT_ACCESS_FILE. Skip anything still carrying the offline
    attribute, and never let one bad file kill the whole zip.
    """
    files = [p for p in src_dir.rglob("*") if p.is_file()]
    total = len(files)
    errors: list[str] = []
    clamped = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for i, p in enumerate(files, 1):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            rel = p.relative_to(src_dir).as_posix()
            try:
                st = p.stat()
                if st.st_file_attributes & _FILE_ATTRIBUTE_OFFLINE:
                    errors.append(f"{rel}: cloud placeholder (skipped)")
                    continue
                dtime, was_clamped = _safe_dt(st.st_mtime)
                clamped += was_clamped
                zi = zipfile.ZipInfo(rel, date_time=dtime)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = (st.st_mode & 0xFFFF) << 16
                with open(p, "rb") as src, \
                        zf.open(zi, "w",
                                force_zip64=st.st_size > 0x7FFF0000) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 512)
            except (OSError, struct.error) as e:
                errors.append(f"{rel}: {e}")
                _emit(on_event, ("log", f"!! zip skip {p.name}: {e}"))
            if i % 200 == 0 or i == total:
                _emit(on_event, ("zip_progress", i, total))
    if clamped:
        _emit(on_event, ("log", f":: {clamped} corrupt timestamps clamped"))
    return errors


def run_backup(*, profile_root: Path, items: list[CatalogItem],
               dest: Path, mode: str, admin: bool,
               speed: str = "full", auto_reset: bool = False,
               on_event: Event_ | None = None,
               cancel: Event | None = None) -> tuple[Path, list[ItemResult]]:
    """Run the backup. Returns (report_path, per-item results)."""
    items = [i for i in items if i.rel_path]
    results: list[ItemResult] = []
    preset = SPEED_PRESETS.get(speed, SPEED_PRESETS["full"])
    letter = profile_root.drive[:1] if profile_root.drive else ""

    staging: Path | None = None
    if mode == "zip":
        # no leading dot — Explorer/cmd handle dotted names poorly
        staging = dest.parent / f"shunt_staging_{dest.stem}"
        _emit(on_event, ("phase", f"staging into {staging} (keeps ACL bypass)"))
        work = staging
    else:
        work = dest
    work.mkdir(parents=True, exist_ok=True)

    try:
        for i, item in enumerate(items, 1):
            if cancel is not None and cancel.is_set():
                break
            src, dst = profile_root / item.rel_path, work / item.rel_path
            _emit(on_event, ("item_start", i, len(items), item.label))
            resets = 0
            while True:
                res = run_item(
                    src, dst, item.rel_path, admin=admin,
                    mt=preset["mt"], ipg=preset["ipg"], zb=preset["zb"],
                    stall_after=STALL_AFTER,
                    extra_xd=item.extra_xd, extra_xf=item.extra_xf,
                    on_line=lambda l: _emit(on_event, ("log", l)),
                    on_file=lambda n: _emit(on_event, ("file", n)),
                    cancel=cancel)
                if not res.stalled or res.cancelled:
                    break
                if auto_reset and letter and resets < MAX_RESETS:
                    resets += 1
                    _emit(on_event, ("phase",
                                     f"{letter}: stalled — resetting "
                                     f"drive ({resets}/{MAX_RESETS})"))
                    if reset_drive(
                            letter,
                            lambda m: _emit(on_event, ("log", m))):
                        _emit(on_event, ("log",
                                         f":: retrying {item.label} "
                                         "(completed files skip, /Z "
                                         "resumes partial file)"))
                        continue
                    _emit(on_event, ("log",
                                     f"!! reset failed — giving up on "
                                     f"{item.label}"))
                else:
                    _emit(on_event, ("log",
                                     "!! stalled — enable 'auto-reset "
                                     "frozen drive' or recovery mode"))
                break
            res.rel_path = item.rel_path
            results.append(res)
            _emit(on_event, ("item_done", item.label, res))
            if res.cancelled:
                break

        write_manifest(work, build_manifest(
            profile_root.name, str(profile_root),
            [it.rel_path for it in items]))

        zip_errors: list[str] = []
        if mode == "zip":
            _emit(on_event, ("phase", "compressing to zip..."))
            zip_errors = _zip_dir(staging, dest, on_event, cancel)
            for e in zip_errors:
                _emit(on_event, ("log", f"   ! {e}"))
    finally:
        if staging is not None:
            _nuke_dir(staging, on_event)

    report = _write_report(dest, mode, profile_root, results, admin,
                           zip_errors)
    _emit(on_event, ("done", report))
    return report, results


def _write_report(dest: Path, mode: str, profile_root: Path,
                  results: list[ItemResult], admin: bool,
                  zip_errors: list[str] | None = None) -> Path:
    lines = [
        "SHUNT backup report",
        f"date          : {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"source profile: {profile_root}",
        f"destination   : {dest}",
        f"mode          : {mode}",
        f"backup mode   : {'/B (elevated)' if admin else 'NOT ELEVATED — ACL errors possible'}",
        "",
        f"{'item':<55} {'files':>7} {'failed':>7}  result",
        "-" * 82,
    ]
    total_fail = 0
    for r in results:
        status = "CANCELLED" if r.cancelled else ("OK" if r.ok else "FAILED")
        lines.append(f"{r.rel_path:<55} {r.files_copied:>7} "
                     f"{r.files_failed:>7}  {status}")
        total_fail += r.files_failed
        for e in r.errors[:50]:
            lines.append(f"    ! {e}")
    if zip_errors:
        lines.append("")
        lines.append("zip stage skips/errors:")
        for e in zip_errors[:200]:
            lines.append(f"    ! {e}")
    lines += ["-" * 82,
              f"failed files total: {total_fail}",
              "",
              "note: browser passwords are DPAPI-encrypted per user and",
              "cannot be migrated — export them from the old machine."]

    path = (dest / REPORT_NAME) if mode == "folder" \
        else dest.with_suffix(".report.txt")
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    return path
