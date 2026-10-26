"""shunt_manifest.json — written into every backup so restore mode can
detect it and map profile-relative paths into the target profile."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

MANIFEST_NAME = "shunt_manifest.json"
REPORT_NAME = "shunt_report.txt"


def build_manifest(source_profile: str, source_root: str,
                   rel_paths: list[str]) -> dict:
    return {
        "shunt_version": "1.0",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "source_profile": source_profile,
        "source_root": source_root,
        "items": [{"path": p} for p in rel_paths],
    }


def write_manifest(dest_dir: Path, manifest: dict) -> Path:
    p = dest_dir / MANIFEST_NAME
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return p


def read_manifest(source: Path) -> dict | None:
    """Read manifest from a backup folder or zip; None if absent."""
    try:
        if source.is_dir():
            p = source / MANIFEST_NAME
            return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        if source.is_file() and source.suffix.lower() == ".zip":
            with ZipFile(source) as zf:
                if MANIFEST_NAME in zf.namelist():
                    return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except Exception:
        pass
    return None
