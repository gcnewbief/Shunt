"""Latest-release check against the GitHub releases API.

/releases/latest already excludes drafts and prereleases. urllib only —
no extra dependency for a once-per-launch call.
"""

from __future__ import annotations

import json
import re
import urllib.request

REPO = "gcnewbief/Shunt"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_TIMEOUT = 5


def latest_release() -> tuple[str, str]:
    """Return (tag, html_url) of the newest stable release."""
    req = urllib.request.Request(_API, headers={"User-Agent": "shunt"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        data = json.loads(r.read())
    return data["tag_name"], data["html_url"]


def _version_tuple(tag: str) -> tuple[int, ...]:
    """'v1.3.1' -> (1, 3, 1); non-numeric noise (v, -beta) is ignored."""
    return tuple(int(p) for p in re.findall(r"\d+", tag)[:4])


def is_newer(tag: str, current: str) -> bool:
    a, b = _version_tuple(tag), _version_tuple(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))
