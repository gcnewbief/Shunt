"""Restore: pull a Shunt backup (folder or zip) into a target profile.

Manifest items are profile-root-relative, so restoring into a differently
named profile just works. Backups without a manifest still restore —
top-level dirs are treated as items.
"""

from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from .copier import ItemResult, run_item
from .manifest import MANIFEST_NAME, read_manifest

Event_ = Callable[[tuple], None]


def _emit(on_event: Event_ | None, evt: tuple) -> None:
    if on_event:
        on_event(evt)


def inspect_backup(source: Path) -> dict:
    """Return {'kind': 'folder'|'zip', 'manifest': dict|None, 'items': [...]}."""
    manifest = read_manifest(source)
    items: list[str] = []

    if manifest:
        items = [i["path"] for i in manifest.get("items", [])]
    elif source.is_dir():
        items = [p.name for p in sorted(source.iterdir())
                 if p.is_dir() and p.name != MANIFEST_NAME]
    elif source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            tops = {n.split("/")[0] for n in zf.namelist()
                    if "/" in n and n.split("/")[0] != MANIFEST_NAME}
            items = sorted(tops)

    kind = "folder" if source.is_dir() else "zip"
    return {"kind": kind, "manifest": manifest, "items": items}


def _safe_member(name: str) -> bool:
    p = Path(name)
    return (not p.is_absolute() and ".." not in p.parts
            and not name.startswith(("/", "\\")))


def _restore_zip(source: Path, rel_items: list[str], dest_profile: Path,
                 on_event: Event_ | None, cancel: Event | None) -> list[str]:
    errors: list[str] = []
    prefixes = tuple(i.rstrip("/") + "/" for i in rel_items)
    with zipfile.ZipFile(source) as zf:
        members = [m for m in zf.infolist()
                   if _safe_member(m.filename)
                   and m.filename.startswith(prefixes)]
        total = len(members)
        for i, m in enumerate(members, 1):
            if cancel is not None and cancel.is_set():
                break
            if m.is_dir():
                continue
            target = dest_profile / m.filename
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as zsrc, open(target, "wb") as out:
                    shutil.copyfileobj(zsrc, out, 1024 * 512)
            except OSError as e:
                errors.append(f"{m.filename}: {e}")
            if i % 25 == 0 or i == total:
                _emit(on_event, ("file", m.filename))
            if i % 100 == 0 or i == total:
                _emit(on_event, ("zip_progress", i, total))
                _emit(on_event, ("log", m.filename))
    return errors


def run_restore(*, backup: Path, kind: str, rel_items: list[str],
                dest_profile: Path, admin: bool,
                on_event: Event_ | None = None,
                cancel: Event | None = None) -> tuple[Path, list[ItemResult]]:
    results: list[ItemResult] = []
    zip_errors: list[str] = []
    dest_profile.mkdir(parents=True, exist_ok=True)

    if kind == "zip":
        _emit(on_event, ("phase", "extracting zip into profile"))
        zip_errors = _restore_zip(backup, rel_items, dest_profile,
                                  on_event, cancel)
        for rel in rel_items:
            results.append(ItemResult(rel_path=rel, exit_code=0))
    else:
        for i, rel in enumerate(rel_items, 1):
            if cancel is not None and cancel.is_set():
                break
            src = backup / rel
            if not src.is_dir():
                continue
            _emit(on_event, ("item_start", i, len(rel_items), rel))
            res = run_item(src, dest_profile / rel, rel, admin=admin,
                           on_line=lambda l: _emit(on_event, ("log", l)),
                           on_file=lambda n: _emit(on_event, ("file", n)),
                           cancel=cancel)
            results.append(res)
            _emit(on_event, ("item_done", rel, res))
            if res.cancelled:
                break

    report = _write_report(backup, dest_profile, results,
                           zip_errors, admin)
    _emit(on_event, ("done", report))
    return report, results


def _write_report(backup: Path, dest_profile: Path,
                  results: list[ItemResult], zip_errors: list[str],
                  admin: bool) -> Path:
    lines = [
        "SHUNT restore report",
        f"date        : {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"backup      : {backup}",
        f"restored to : {dest_profile}",
        f"backup mode : {'/B (elevated)' if admin else 'not elevated'}",
        "",
    ]
    for r in results:
        status = "CANCELLED" if r.cancelled else ("OK" if r.ok else "FAILED")
        lines.append(f"{r.rel_path:<60} {r.files_copied:>7} files  {status}")
        for e in r.errors[:50]:
            lines.append(f"    ! {e}")
    for e in zip_errors[:200]:
        lines.append(f"    ! {e}")
    lines += ["",
              "reminder: browser passwords are DPAPI-encrypted per user;",
              "import a password export from the old machine instead."]

    path = dest_profile / "shunt_restore_report.txt"
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        path = Path.home() / "Desktop" / "shunt_restore_report.txt"
        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass
    return path
