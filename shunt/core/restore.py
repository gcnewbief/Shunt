"""Restore: pull a Shunt backup (folder or zip) into a target profile.

Manifest items are profile-root-relative, so restoring into a differently
named profile just works. Backups without a manifest still restore —
top-level dirs are treated as items, but sources that look like a drive
root (D:\\ with Windows/Program Files/Users…) are refused outright: a
real incident showed that path dumps an entire drive — junction loops
and all — into the target profile.
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

# dirs that signal "this is a drive root / OS install", not a backup
_DRIVE_ROOT_HINTS = frozenset({
    "windows", "program files", "program files (x86)", "programdata",
    "documents and settings", "system volume information",
    "$recycle.bin", "perflogs", "recovery", "config.msi",
})


def _emit(on_event: Event_ | None, evt: tuple) -> None:
    if on_event:
        on_event(evt)


def _is_reparse(p: Path) -> bool:
    """Junctions and dir symlinks — never copy *through* one as a source.
    Must lstat: stat() follows the link and reports the target's attrs."""
    try:
        return bool(p.lstat().st_file_attributes & 0x400)  # REPARSE_POINT
    except (OSError, AttributeError):
        return p.is_symlink()


def _looks_like_drive_root(source: Path) -> bool:
    try:
        names = {p.name.lower() for p in source.iterdir()}
    except OSError:
        return False
    return len(names & _DRIVE_ROOT_HINTS) >= 3


def inspect_backup(source: Path) -> dict:
    """Return {'kind': 'folder'|'zip', 'manifest': dict|None, 'items': [...]}."""
    manifest = read_manifest(source)
    items: list[str] = []

    if manifest:
        items = [i["path"] for i in manifest.get("items", [])]
    elif source.is_dir():
        resolved = source.resolve()
        if resolved.parent == resolved:
            raise ValueError(
                f"{source} is a drive root, not a backup — pick the "
                "backup folder inside it")
        if _looks_like_drive_root(source):
            raise ValueError(
                f"{source} has no manifest and looks like a drive root "
                "(Windows/Program Files/Users…) — refusing to treat it "
                "as a backup; pick the backup folder instead")
        items = [p.name for p in sorted(source.iterdir())
                 if p.is_dir() and p.name != MANIFEST_NAME
                 and not _is_reparse(p)]
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
                 on_event: Event_ | None, cancel: Event | None,
                 overwrite: bool) -> list[str]:
    errors: list[str] = []
    skipped = 0
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
                if not overwrite and target.exists() \
                        and target.stat().st_size == m.file_size:
                    skipped += 1
                    continue
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
    if skipped:
        _emit(on_event, ("log", f"skipped {skipped} identical existing files"))
    return errors


def run_restore(*, backup: Path, kind: str, rel_items: list[str],
                dest_profile: Path, admin: bool, overwrite: bool = False,
                on_event: Event_ | None = None,
                cancel: Event | None = None) -> tuple[Path, list[ItemResult]]:
    results: list[ItemResult] = []
    zip_errors: list[str] = []

    if backup.is_dir():
        b, d = backup.resolve(), dest_profile.resolve()
        if b == d or b in d.parents or d in b.parents:
            raise ValueError(
                f"backup ({b}) and target profile ({d}) overlap — "
                "the copy would recurse; refusing to run")
        if _looks_like_drive_root(b):
            raise ValueError(
                f"{b} looks like a drive root (Windows/Program Files/"
                "Users…), not a backup folder — refusing to restore "
                "from it")

    safe_items = [r for r in rel_items
                  if not Path(r).is_absolute() and ".." not in Path(r).parts]
    if len(safe_items) != len(rel_items):
        _emit(on_event, ("log", "!! dropped item(s) with unsafe paths"))

    dest_profile.mkdir(parents=True, exist_ok=True)

    if kind == "zip":
        _emit(on_event, ("phase", "extracting zip into profile"))
        zip_errors = _restore_zip(backup, safe_items, dest_profile,
                                  on_event, cancel, overwrite)
        for rel in safe_items:
            results.append(ItemResult(rel_path=rel, exit_code=0))
    else:
        for i, rel in enumerate(safe_items, 1):
            if cancel is not None and cancel.is_set():
                break
            src = backup / rel
            if not src.is_dir():
                continue
            if _is_reparse(src):
                _emit(on_event, ("log", f"!! {rel}: junction/reparse "
                                        "point — skipped"))
                results.append(ItemResult(
                    rel_path=rel, exit_code=0,
                    errors=["skipped: junction/reparse-point source"]))
                _emit(on_event, ("item_done", rel, results[-1]))
                continue
            _emit(on_event, ("item_start", i, len(safe_items), rel))
            res = run_item(src, dest_profile / rel, rel, admin=admin,
                           overwrite=overwrite,
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
