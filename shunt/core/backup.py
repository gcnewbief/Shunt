"""Backup orchestration: profile root -> folder or zip, with manifest+report.

Zip mode stages via robocopy first so /B still bypasses ACLs on attached
drives, then compresses the staging dir.
"""

from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from .catalog import CatalogItem
from .copier import ItemResult, run_item
from .manifest import REPORT_NAME, build_manifest, write_manifest

Event_ = Callable[[tuple], None]


def _emit(on_event: Event_ | None, evt: tuple) -> None:
    if on_event:
        on_event(evt)


def _zip_dir(src_dir: Path, zip_path: Path,
             on_event: Event_ | None, cancel: Event | None) -> None:
    files = [p for p in src_dir.rglob("*") if p.is_file()]
    total = len(files)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for i, p in enumerate(files, 1):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            zf.write(p, p.relative_to(src_dir).as_posix())
            if i % 200 == 0 or i == total:
                _emit(on_event, ("zip_progress", i, total))


def run_backup(*, profile_root: Path, items: list[CatalogItem],
               dest: Path, mode: str, admin: bool,
               on_event: Event_ | None = None,
               cancel: Event | None = None) -> tuple[Path, list[ItemResult]]:
    """Run the backup. Returns (report_path, per-item results)."""
    items = [i for i in items if i.rel_path]
    results: list[ItemResult] = []

    staging: Path | None = None
    if mode == "zip":
        staging = dest.parent / f".{dest.stem}.staging"
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
            res = run_item(src, dst, item.rel_path, admin=admin,
                           extra_xd=item.extra_xd, extra_xf=item.extra_xf,
                           on_line=lambda l: _emit(on_event, ("log", l)),
                           on_file=lambda n: _emit(on_event, ("file", n)),
                           cancel=cancel)
            res.rel_path = item.rel_path
            results.append(res)
            _emit(on_event, ("item_done", item.label, res))
            if res.cancelled:
                break

        write_manifest(work, build_manifest(
            profile_root.name, str(profile_root),
            [it.rel_path for it in items]))

        if mode == "zip":
            _emit(on_event, ("phase", "compressing to zip..."))
            _zip_dir(staging, dest, on_event, cancel)
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    report = _write_report(dest, mode, profile_root, results, admin)
    _emit(on_event, ("done", report))
    return report, results


def _write_report(dest: Path, mode: str, profile_root: Path,
                  results: list[ItemResult], admin: bool) -> Path:
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
