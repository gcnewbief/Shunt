"""Verify _nuke_dir deletes trees deeper than MAX_PATH."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shunt.core.backup import _nuke_dir

base = Path(__file__).resolve().parent / "_nuke_tmp"
base.mkdir(exist_ok=True)
cur = base
for _ in range(30):                     # ~500 chars deep
    cur = cur / "very_deep_dir_"
    os.mkdir(str(cur.resolve()))        # extended \\?\ form; makedirs() can't do this
with open(str((cur / "file.txt").resolve()), "w") as f:
    f.write("x")
print("path length:", len(str(cur / "file.txt")), "(MAX_PATH = 260)")
assert len(str(cur / "file.txt")) > 260

_nuke_dir(base)
assert not base.exists(), "dir still there"
print("NUKE OK - >260-char tree deleted")
