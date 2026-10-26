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

    return items
