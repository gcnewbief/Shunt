"""Locate user profiles on a source path (attached drive, Users dir, etc.)."""

from __future__ import annotations

import string
from pathlib import Path

from .catalog import STANDARD_DIRS

_SKIP = {"default", "default user", "all users", "public",
         "defaultapppool", ".net v4.5", ".net v4.5 classic"}


def looks_like_profile(path: Path) -> bool:
    try:
        if (path / "ntuser.dat").is_file():
            return True
        hits = sum(1 for d in STANDARD_DIRS if (path / d).is_dir())
        return hits >= 2
    except OSError:
        return False


def enumerate_profiles(source: Path) -> list[Path]:
    """Return candidate profile dirs, newest-modified first.

    Accepts: a drive root (D:\\), a Users dir (D:\\Users), or a
    profile dir itself (D:\\Users\\John).
    """
    source = source.resolve()
    profiles: list[Path] = []

    if looks_like_profile(source):
        profiles.append(source)
    else:
        users_dir = source / "Users" if (source / "Users").is_dir() else source
        try:
            for child in users_dir.iterdir():
                if (child.is_dir() and child.name.lower() not in _SKIP
                        and looks_like_profile(child)):
                    profiles.append(child)
        except OSError:
            pass

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(profiles, key=mtime, reverse=True)


def drive_candidates() -> list[str]:
    """Drive roots that contain a Users dir — quick-pick list for the UI."""
    out = []
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:/Users")
        try:
            if p.is_dir():
                out.append(f"{letter}:\\Users")
        except OSError:
            pass
    return out
