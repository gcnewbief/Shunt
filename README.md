# SHUNT

Technician's data-migration tool for Windows. Pull user data off old
drives / profiles and shunt it into a new machine — without paying for
EaseUS and without Explorer's ACL purgatory.

## Run

```
py shunt_app.py          # or: py -m shunt
```

Say yes to the UAC prompt. Admin isn't strictly required, but without it
robocopy runs *without* `/B` and ACL-denied folders on an attached drive
will fail into the report instead of copying silently.

## Build the exe

```
py build.py              # -> dist\Shunt.exe (single file, self-elevating)
```

## Why it's fast

The copy engine is `robocopy`, driven per-item:

```
robocopy <src> <dst> /E /COPY:DAT /DCOPY:T /B /R:1 /W:1 /XJ /XA:O /MT:16 /NP
```

- `/B` — backup mode. Reads/writes via SeBackupPrivilege, bypassing the
  per-file ACL negotiation that makes Explorer take an eternity.
- `/COPY:DAT` — no ACLs copied. Old-drive SIDs are meaningless on the new
  machine; files inherit the destination profile's permissions.
- `/XJ` — skips junction points (`My Music`, AppData junction loops).
- `/XA:O` — skips OneDrive on-demand placeholders.
- `/R:1 /W:1` — locked files fail fast and land in the report.
- `/MT:16` — 16-thread copy.

## What it copies

Pre-ticked: Desktop, Documents, Downloads, Pictures, Videos, Music,
Favorites (including OneDrive-redirected variants).

Selectable AppData subtree: Chrome, Edge, Firefox, Thunderbird, Brave,
Opera, Outlook signatures, Sticky Notes — browser cache dirs excluded.

## Backup → restore round trip

Every backup (folder or zip) carries `shunt_manifest.json` listing the
source profile and profile-relative paths. The RESTORE tab detects it
and maps each item into the target profile — different username is fine.

Zip mode stages through robocopy first, so `/B` still applies before
compression.

## Known limits

- **Browser passwords don't migrate.** Chrome/Edge encrypt them with the
  old user's DPAPI key; the copied profile is unreadable for passwords on
  the new machine. Bookmarks/history/extensions are fine. Export
  passwords on the old machine first.
- Restoring into a live profile: close browsers/Outlook first or their
  locked files will fail into the report.
- No VSS — files locked on a running source system are skipped.

## Failing drives — gentle / recovery mode

The Backup tab has a SPEED selector:

- **full** — `/MT:16`, no throttle. Healthy drives.
- **gentle** — single-threaded + `/IPG:10` (~6 MB/s). Less I/O pressure,
  less heat.
- **recovery** — single-threaded + `/IPG:50` (~1 MB/s) + `/ZB`
  restartable, so a partially-copied file resumes mid-file after a
  reset instead of starting over.

**Freeze watchdog:** every copy monitors the robocopy process's actual
I/O byte counter (not output silence — a big file copies quietly for
minutes). ~3 minutes of zero I/O = frozen → the item is failed, not the
whole run.

**auto-reset frozen drive:** on a stall, the source disk's PNP device is
disabled/re-enabled via `pnputil` (forces re-enumeration — works on most
USB docks; actual power-cycle is dock-dependent), waits for the volume,
then retries the item. `/Z` + identical-skip means it resumes where it
left off. Up to 5 resets per item, then it fails into the report. The
"reset drive now" button does a manual reset on the source drive letter.
System volume is never reset.

Tip: recovery mode + tick the most valuable folders first (Documents,
browsers) — the tree order is roughly importance-first already.

## Reports

`shunt_report.txt` (backups) / `shunt_restore_report.txt` (restores)
list every copied/failed/skipped item plus per-file errors — show the
customer exactly what did and didn't come across.
