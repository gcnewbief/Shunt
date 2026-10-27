<img src="assets/shunt.png" align="right" width="96">

# SHUNT

Technician's data-migration tool for Windows. Pull user data off old
customer drives or live machines and shunt it into a new profile —
without paying for EaseUS and without Explorer's ACL purgatory.

Built for repair-shop bench work: attached old drives, dying USB disks,
OneDrive placeholders, 500-char browser-profile paths, and customers who
want a report, not an error dialog.

## Download

**[Get the latest Shunt.exe →](https://github.com/gcnewbief/Shunt/releases/latest)**

Single file, no install. Run it, say yes to UAC (needed for ACL-bypass
copies off attached drives).

## Quick start

1. Plug the old drive into your dock, note the drive letter.
2. Launch `Shunt.exe` → **BACKUP** tab.
3. Pick the drive under *source* → pick the profile → the tree ticks
   the usual suspects automatically.
4. Choose a destination folder, or *zip* for a portable archive.
5. **START BACKUP** — watch the green console, read the report when done.
6. On the new machine: **RESTORE** tab → pick the backup → pick the
   target profile (different username is fine) → **START RESTORE**.

Full walkthrough with the failing-drive workflow:
**[docs/USAGE.md](docs/USAGE.md)**

## What it copies

- **Pre-ticked:** Desktop, Documents, Downloads, Pictures, Videos,
  Music, Favorites — including OneDrive-redirected variants.
- **AppData (programs):** curated set — Chrome, Edge, Firefox,
  Thunderbird, Brave, Opera, Outlook signatures, Sticky Notes.
- **Discovered browsers (auto):** fingerprints `User Data`+`Local State`
  (Chromium family) and `profiles.ini` (Gecko family) so Vivaldi, Arc,
  Zen, and forks show up too — flagged for review.
- **Excluded:** `ntuser.dat`, browser caches, junctions, `*.ost`,
  OneDrive offline placeholders.

## Why it's fast

The engine is `robocopy`, driven per-item:

```
robocopy <src> <dst> /E /COPY:DAT /DCOPY:T /B /R:1 /W:1 /XJ /XA:O /MT:16 /NP
```

- `/B` — backup mode via SeBackupPrivilege; bypasses per-file ACL
  negotiation that makes Explorer take an eternity.
- `/COPY:DAT` — no ACLs copied. Old-drive SIDs are meaningless on the
  new machine; files inherit the destination profile's permissions.
- `/XJ` — skips junction loops. `/XA:O` — skips OneDrive stubs.
- `/R:1 /W:1` — locked files fail fast into the report, not a hang.
- `/MT:16` — 16 threads.

## Failing drives

SPEED selector on the Backup tab:

| mode | behavior |
|---|---|
| **full** | `/MT:16`, no throttle — healthy drives |
| **gentle** | single-threaded, `/IPG:10` (~6 MB/s) |
| **recovery** | single-threaded, `/IPG:50` (~1 MB/s), `/ZB` restartable — a partially-copied file resumes mid-file after a reset |

A freeze watchdog watches the robocopy process's real I/O counter —
~3 minutes of zero I/O fails the item, not the run. **auto-reset** will
`pnputil` disable/re-enable the source disk (dock-dependent power cycle)
and retry up to 5 times; "reset drive now" does it manually. C: is
never touched.

## Reports

Every run writes `shunt_report.txt` / `shunt_restore_report.txt` —
copied, skipped, failed, and why. Show the customer.

## Known limits

- **Browser passwords don't migrate** — DPAPI keys are per-user.
  Bookmarks/history/extensions are fine; export passwords on the old
  machine first.
- Restore into a live profile: close browsers/Outlook first or locked
  files land in the report.
- No VSS — files locked on a running source system are skipped.

## Development

```
py shunt_app.py            # run from source
py build.py                # -> dist\Shunt.exe (one-file, self-elevating)
py tests/smoke_engine.py   # engine round-trip suite
py tests/nuke_check.py     # long-path staging cleanup check
py tools/make_icon.py      # regenerate the dozer
```

Stuck staging folder in the field? `tools/kill-staging.ps1`.
Architecture and pitfalls: `AGENTS.md`.
