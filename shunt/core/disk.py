"""Failing-drive support: locate a volume's disk device and reset it.

Reset = pnputil disable/enable on the disk's PNP instance, which forces
re-enumeration. Works on most USB docks/enclosures; whether it actually
power-cycles depends on the dock's firmware, but a bus re-enumeration
alone unfreezes a surprising number of hung SSDs.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

_NO_WIN = 0x08000000


def _ps(cmd: str, timeout: int = 30) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
        creationflags=_NO_WIN)
    return r.stdout.strip()


def disk_pnp_id(letter: str) -> str | None:
    """PNP instance id of the physical disk behind a drive letter."""
    letter = letter.rstrip(":\\/").upper()
    try:
        num = _ps(f"(Get-Partition -DriveLetter {letter} | "
                  f"Get-Disk).Number")
        if not num.isdigit():
            return None
        return _ps(
            f"(Get-CimInstance Win32_DiskDrive | "
            f"Where-Object {{$_.Index -eq {num}}}).PNPDeviceID") or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def is_system_volume(letter: str) -> bool:
    return letter.rstrip(":\\/").upper() == \
        os.environ.get("SystemDrive", "C:").rstrip(":").upper()


def reset_drive(letter: str,
                on_log: Callable[[str], None] | None = None) -> bool:
    """Disable/enable the disk device and wait for the volume back."""
    letter = letter.rstrip(":\\/").upper()
    log = on_log or (lambda _m: None)

    if is_system_volume(letter):
        log("!! refusing to reset the system volume")
        return False

    dev = disk_pnp_id(letter)
    if not dev:
        log(f"!! can't resolve disk device for {letter}:")
        return False
    log(f":: {letter}: disk device = {dev}")

    try:
        subprocess.run(["pnputil", "/disable-device", dev],
                       capture_output=True, timeout=30,
                       creationflags=_NO_WIN)
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"!! disable failed: {e}")
        return False

    log(f":: {letter}: device disabled — waiting, then re-enabling")
    time.sleep(4)
    try:
        subprocess.run(["pnputil", "/enable-device", dev],
                       capture_output=True, timeout=30,
                       creationflags=_NO_WIN)
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"!! enable failed: {e}")
        return False

    # wait for the volume to re-enumerate (up to ~45s)
    probe = Path(f"{letter}:/")
    for _ in range(45):
        time.sleep(1)
        try:
            if probe.exists():
                log(f":: {letter}: volume back online")
                return True
        except OSError:
            pass
    log(f"!! {letter}: device re-enabled but volume hasn't returned")
    return False
