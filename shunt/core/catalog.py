"""Catalog of migratable items inside a user profile.

Everything is profile-root-relative so a backup can be restored into a
differently-named profile on the new machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# dirs excluded when copying browser profiles (regenerable cache)
BROWSER_CACHE_XD = (
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
    "DawnGraphiteCache", "DawnWebGPUCache", "Media Cache",
    "Service Worker", "Crashpad", "component_crx_cache",
    "optimization_guide_model_store",
)

STANDARD_DIRS = ("Desktop", "Documents", "Downloads", "Pictures",
                 "Videos", "Music", "Favorites")

# label, rel path under profile, default-checked, extra dir excludes, note
_APPDATA_SPECS = (
    ("Google Chrome", "AppData/Local/Google/Chrome/User Data", True,
     BROWSER_CACHE_XD, "bookmarks/history/extensions — NOT passwords"),
    ("Microsoft Edge", "AppData/Local/Microsoft/Edge/User Data", True,
     BROWSER_CACHE_XD, "bookmarks/history/extensions — NOT passwords"),
    ("Mozilla Firefox", "AppData/Roaming/Mozilla/Firefox", True, (),
     "full profile"),
    ("Thunderbird mail", "AppData/Roaming/Thunderbird", True, (),
     "mail + address book"),
    ("Outlook signatures", "AppData/Roaming/Microsoft/Signatures", True, (), ""),
    ("Brave", "AppData/Local/BraveSoftware/Brave-Browser/User Data", False,
     BROWSER_CACHE_XD, ""),
    ("Opera", "AppData/Roaming/Opera Software/Opera Stable", False,
     BROWSER_CACHE_XD, ""),
    ("Sticky Notes", "AppData/Local/Packages/"
     "Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe", True, (),
     "may not exist on Win10-era drives"),
)


@dataclass
class CatalogItem:
    label: str
    rel_path: str | None = None            # None => container/group node
    checked: bool = True
    note: str = ""
    extra_xd: tuple = ()
    extra_xf: tuple = ()
    children: list["CatalogItem"] = field(default_factory=list)


# vendor dirs that can't contain browsers — skip for scan speed/noise
_SKIP_VENDORS = {"microsoft", "windows", "packages", "temp", "cache",
                 "application verifier", "common files"}


def _looks_chromium(ud: Path) -> bool:
    if (ud / "Local State").is_file():
        return True
    try:
        return any(c.is_dir() and
                   (c.name == "Default" or c.name.startswith("Profile"))
                   for c in ud.iterdir())
    except OSError:
        return False


def _discover_browsers(profile_root: Path, known: set[str]) -> list[CatalogItem]:
    """Find browsers the spec list doesn't know about.

    Chromium family: <vendor>/<product>/User Data + Local State/Profile dirs
    Gecko family:    <vendor>/profiles.ini or <vendor>/<product>/profiles.ini
    """
    found: list[CatalogItem] = []
    for base_rel in ("AppData/Local", "AppData/Roaming"):
        base = profile_root / base_rel
        if not base.is_dir():
            continue
        try:
            vendors = [v for v in base.iterdir()
                       if v.is_dir() and v.name.lower() not in _SKIP_VENDORS]
        except OSError:
            continue
        for vendor in vendors:
            candidates = [vendor]
            try:
                candidates += [p for p in vendor.iterdir() if p.is_dir()]
            except OSError:
                pass
            for prod in candidates:
                ud = prod / "User Data"
                if ud.is_dir() and _looks_chromium(ud):
                    rel = ud.relative_to(profile_root).as_posix()
                    if rel not in known:
                        found.append(CatalogItem(
                            label=f"{prod.name} (Chromium)",
                            rel_path=rel, note="auto-discovered — review",
                            extra_xd=BROWSER_CACHE_XD))
                        known.add(rel)
                elif (prod / "profiles.ini").is_file():
                    rel = prod.relative_to(profile_root).as_posix()
                    if rel not in known:
                        found.append(CatalogItem(
                            label=f"{prod.name} (Gecko)",
                            rel_path=rel,
                            note="auto-discovered — review"))
                        known.add(rel)
    return found


def _resolve_onedrive(root: Path, name: str) -> str | None:
    """Return rel path for a standard dir, probing OneDrive-redirected variants."""
    if (root / name).is_dir():
        return name
    for od in sorted(root.glob("OneDrive*")):
        if (od / name).is_dir():
            return f"{od.name}/{name}"
    return None


def build(profile_root: Path) -> list[CatalogItem]:
    """Catalog items that actually exist under *profile_root*."""
    items: list[CatalogItem] = []

    for name in STANDARD_DIRS:
        rel = _resolve_onedrive(profile_root, name)
        if rel:
            label = name if rel == name else f"{name} (OneDrive)"
            items.append(CatalogItem(label=label, rel_path=rel))

    if (profile_root / "Saved Games").is_dir():
        items.append(CatalogItem(label="Saved Games", rel_path="Saved Games",
                                 checked=False))

    appdata = []
    for label, rel, checked, extra_xd, note in _APPDATA_SPECS:
        if (profile_root / rel).is_dir():
            appdata.append(CatalogItem(label=label, rel_path=rel,
                                       checked=checked, note=note,
                                       extra_xd=extra_xd))
    if appdata:
        items.append(CatalogItem(label="AppData (programs)", children=appdata))

    # auto-discovered browsers live in their own group — ticked but
    # clearly marked for review, keeping the curated section tidy
    discovered = _discover_browsers(
        profile_root, {c.rel_path for c in appdata})
    if discovered:
        items.append(CatalogItem(
            label="Discovered browsers (auto)", children=discovered))

    return items
