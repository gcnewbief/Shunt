"""Nuker engine checks — extended-path rename, refuse_reason, full nuke."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shunt.core.nuker import _ext, nuke_dir, refuse_reason

ok = True


def check(cond: bool, msg: str) -> None:
    global ok
    print(("  PASS " if cond else "  FAIL ") + msg)
    ok = ok and cond


# --- \\?\ rename works (the relocate step depends on it) ---
t = Path("tests/_relocate_tmp")
(t / "sub").mkdir(parents=True, exist_ok=True)
(t / "f.txt").write_bytes(b"x")
try:
    os.rename(_ext(t), _ext(Path("C:/_shunt_relocate_test")))
    moved = Path("C:/_shunt_relocate_test").exists()
except OSError as e:
    moved = False
    print("   rename err:", e)
check(moved, "os.rename on \\\\?\\ extended paths")
shutil.rmtree("C:/_shunt_relocate_test", ignore_errors=True)

# --- refuse_reason ---
cases = [
    ("C:\\", "drive root refused"),
    ("C:\\Windows", "C:\\Windows refused"),
    ("C:\\Users", "C:\\Users refused"),
    (os.environ["USERPROFILE"], "own profile refused"),
    ("C:\\Users\\Gcnewbief\\Desktop\\junk", "normal dir allowed"),
    ("C:\\Users\\Gcnewbief\\Desktop\\Windows", "Windows subdir allowed"),
]
for p, msg in cases:
    refused = refuse_reason(Path(p)) is not None
    want = "refused" in msg
    check(refused == want, f"{msg} ({p})")

# --- full nuke: deep tree + junction-to-outside + bad attrs ---
base = Path("tests/_nuke_engine_tmp")
outside = Path("tests/_outside_guard"); (outside / "keep").mkdir(parents=True, exist_ok=True)
(outside / "keep" / "precious.txt").write_bytes(b"precious")

cur = base
base.mkdir(parents=True, exist_ok=True)
for _ in range(30):
    cur = cur / "very_deep_dir_"
    os.mkdir(str(cur.resolve()))
(cur / "deep.txt").write_bytes(b"x")
os.chmod(str((cur / "deep.txt").resolve()), 0o400)

(base / "escape_junction").parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["cmd", "/c", "mklink", "/J",
                str(base / "escape_junction"),
                str(outside.resolve())], capture_output=True)

events = []
report, success = nuke_dir(base, admin=False, on_event=events.append)

check(success, "nuke_dir deleted the tree")
check(not base.exists(), "tree gone")
check((outside / "keep" / "precious.txt").is_file(),
      "junction target OUTSIDE tree untouched")
check(any(e[0] == "nuke_progress" for e in events),
      "progress events emitted")
check(any(e[0] == "nuke_count" for e in events),
      "scan count events emitted")
check(report.is_file(), "report written")

shutil.rmtree(outside, ignore_errors=True)
print("\nALL OK" if ok else "\nFAILURES", flush=True)
sys.exit(0 if ok else 1)
