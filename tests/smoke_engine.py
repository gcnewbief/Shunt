"""Engine smoke test — builds a mock old drive, runs backup (folder+zip)
and restore round-trip without the GUI.

    py tests/smoke_engine.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shunt.core import backup as backup_core
from shunt.core import catalog, profiles, restore as restore_core
from shunt.elevate import is_admin

ROOT = Path(__file__).resolve().parent
MOCK = ROOT / "mock_drive"
PROFILE = MOCK / "Users" / "TestUser"


def seed() -> None:
    shutil.rmtree(MOCK, ignore_errors=True)

    def w(rel: str, data: bytes = b"x" * 4096) -> None:
        p = PROFILE / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    w("ntuser.dat")                                     # must never copy
    w("Documents/notes.txt", b"hello")
    w("Documents/sub/deep.txt", b"deep")
    w("Pictures/img1.jpg")
    w("Desktop/shortcut.lnk")
    w("Downloads/big.bin", b"z" * 200_000)
    w("Videos/clip.mp4")
    w("Music/track.mp3")
    w("AppData/Local/Google/Chrome/User Data/Default/Bookmarks", b"{}")
    w("AppData/Local/Google/Chrome/User Data/Default/Cache/junk.bin")
    w("AppData/Local/Vivaldi/User Data/Local State", b"{}")
    w("AppData/Local/Vivaldi/User Data/Default/Bookmarks", b"{}")
    w("AppData/Roaming/zen/profiles.ini", b"[Profile0]")
    w("AppData/Roaming/zen/Profiles/abc.default/places.sqlite")
    w("AppData/Roaming/Mozilla/Firefox/Profiles/x.default/places.sqlite")
    w("AppData/Roaming/Microsoft/Signatures/sig.htm")
    w("AppData/Local/Packages/Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe/"
      "LocalState/plum.sqlite")

    # classic junction loop: Documents\My Music -> ..\Music
    subprocess.run(["cmd", "/c", "mklink", "/J",
                    str(PROFILE / "Documents" / "My Music"),
                    str(PROFILE / "Music")],
                   capture_output=True)

    # denied-ACL dir — only /B gets through this
    denied = PROFILE / "Documents" / "LockedOut"
    denied.mkdir(exist_ok=True)
    (denied / "secret.txt").write_bytes(b"acl-protected")
    subprocess.run(["icacls", str(denied), "/deny", "Everyone:(OI)(CI)F"],
                   capture_output=True)


def check(cond: bool, msg: str, failures: list) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def main() -> int:
    admin = is_admin()
    print(f"admin={admin}")
    seed()
    failures: list[str] = []

    print("\n== profiles ==")
    found = profiles.enumerate_profiles(MOCK / "Users")
    check(PROFILE in found, "enumerates TestUser profile", failures)

    print("\n== catalog ==")
    items = catalog.build(PROFILE)
    labels = {i.label for i in items}
    check("Documents" in labels, "Documents present", failures)
    groups = [i for i in items if i.children]
    spec_grp = next(g for g in groups if g.label.startswith("AppData"))
    disc_grp = next(g for g in groups if g.label.startswith("Discovered"))
    spec_labels = {c.label for c in spec_grp.children}
    disc_labels = {c.label for c in disc_grp.children}
    check("Google Chrome" in spec_labels, "Chrome item present", failures)
    check(any("Vivaldi" in l for l in disc_labels),
          "Vivaldi auto-discovered (Chromium)", failures)
    check(any("zen" in l for l in disc_labels),
          "zen auto-discovered (Gecko)", failures)
    check(not any("Vivaldi" in l for l in spec_labels),
          "discovery not mixed into curated group", failures)

    leaves = [i for i in items if i.rel_path] + \
        [c for i in items for c in i.children if c.rel_path]
    events: list = []
    work = Path(tempfile.mkdtemp(prefix="shunt_test_"))

    print("\n== backup: folder ==")
    dest_folder = work / "backup_folder"
    report, results = backup_core.run_backup(
        profile_root=PROFILE, items=leaves, dest=dest_folder,
        mode="folder", admin=admin, on_event=events.append)
    check((dest_folder / "Documents/notes.txt").is_file(),
          "notes.txt copied", failures)
    check((dest_folder / "shunt_manifest.json").is_file(),
          "manifest written", failures)
    check(report.is_file(), "report written", failures)
    check(not (dest_folder / "ntuser.dat").exists(),
          "ntuser.dat not copied", failures)
    check(not (dest_folder / "Documents/My Music/track.mp3").exists(),
          "junction not traversed", failures)
    check(not (dest_folder /
               "AppData/Local/Google/Chrome/User Data/Default/Cache"
               ).exists(), "browser cache excluded", failures)
    locked = dest_folder / "Documents/LockedOut/secret.txt"
    check(locked.is_file() == admin,
          f"denied-ACL file {'copied via /B' if admin else 'skipped without /B'}",
          failures)

    print("\n== backup: zip ==")
    dest_zip = work / "backup.zip"
    backup_core.run_backup(profile_root=PROFILE, items=leaves,
                           dest=dest_zip, mode="zip", admin=admin,
                           on_event=events.append)
    check(dest_zip.is_file(), "zip written", failures)
    check(dest_zip.with_suffix(".report.txt").is_file(),
          "zip report written", failures)
    info = restore_core.inspect_backup(dest_zip)
    check(info["manifest"] is not None, "manifest readable from zip", failures)
    check("Documents" in info["items"], "Documents in zip items", failures)

    print("\n== restore: zip -> differently-named profile ==")
    dest_profile = work / "NewUser"
    report2, _ = restore_core.run_restore(
        backup=dest_zip, kind="zip",
        rel_items=[i["path"] for i in info["manifest"]["items"]],
        dest_profile=dest_profile, admin=admin, on_event=events.append)
    check((dest_profile / "Documents/notes.txt").is_file(),
          "zip restore: notes.txt", failures)
    check((dest_profile /
           "AppData/Roaming/Mozilla/Firefox/Profiles/x.default/places.sqlite"
           ).is_file(), "zip restore: firefox profile", failures)
    check(report2.is_file(), "restore report written", failures)

    print("\n== restore: folder ==")
    dest_profile2 = work / "NewUser2"
    info2 = restore_core.inspect_backup(dest_folder)
    restore_core.run_restore(
        backup=dest_folder, kind="folder",
        rel_items=[i["path"] for i in info2["manifest"]["items"]],
        dest_profile=dest_profile2, admin=admin, on_event=events.append)
    check((dest_profile2 / "Documents/sub/deep.txt").is_file(),
          "folder restore: deep.txt", failures)

    print("\n== restore safety ==")
    # manifest-less dir that looks like a drive root must be refused
    fake_root = work / "fake_drive"
    for d in ("Windows", "Program Files", "ProgramData", "Users",
              "System Volume Information", "$Recycle.Bin"):
        (fake_root / d).mkdir(parents=True, exist_ok=True)
    try:
        restore_core.inspect_backup(fake_root)
        check(False, "drive-root folder refused at inspect", failures)
    except ValueError:
        check(True, "drive-root folder refused at inspect", failures)
    try:
        restore_core.run_restore(backup=fake_root, kind="folder",
                                 rel_items=["Users"],
                                 dest_profile=work / "Victim",
                                 admin=admin, on_event=events.append)
        check(False, "drive-root folder refused at run", failures)
    except ValueError:
        check(True, "drive-root folder refused at run", failures)

    # destination inside the backup would recurse — refuse
    try:
        restore_core.run_restore(backup=dest_folder, kind="folder",
                                 rel_items=["Documents"],
                                 dest_profile=dest_folder / "Inner",
                                 admin=admin, on_event=events.append)
        check(False, "dest-inside-backup refused", failures)
    except ValueError:
        check(True, "dest-inside-backup refused", failures)

    # junction as a top-level item is neither listed nor copied
    raw = work / "raw_backup"
    (raw / "Docs").mkdir(parents=True)
    (raw / "Docs" / "f.txt").write_bytes(b"hi")
    subprocess.run(["cmd", "/c", "mklink", "/J",
                    str(raw / "Loop"), str(raw)], capture_output=True)
    info3 = restore_core.inspect_backup(raw)
    check("Loop" not in info3["items"],
          "junction excluded from inspect items", failures)
    check("Docs" in info3["items"],
          "manifest-less dir restore still lists dirs", failures)
    _, results3 = restore_core.run_restore(
        backup=raw, kind="folder", rel_items=["Docs", "Loop"],
        dest_profile=work / "RawTarget", admin=admin,
        on_event=events.append)
    loop_res = next(r for r in results3 if r.rel_path == "Loop")
    check(bool(loop_res.errors), "junction source skipped at copy",
          failures)
    check((work / "RawTarget" / "Docs" / "f.txt").is_file(),
          "raw dir item still restored", failures)
    check(not (work / "RawTarget" / "Loop").exists(),
          "no Loop dir created in dest", failures)

    # cleanup: strip deny ACE so the mock can be deleted later
    subprocess.run(["icacls", str(PROFILE / "Documents" / "LockedOut"),
                    "/remove:d", "Everyone"], capture_output=True)

    print(f"\n{len(failures)} failure(s)")
    for f in failures:
        print(" -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
