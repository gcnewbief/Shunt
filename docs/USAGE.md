# SHUNT — Bench Guide

Step-by-step for the two jobs this tool does: **backing up** an old
drive/profile and **restoring** it into a new machine's user profile.

---

## Before you start

- Copy `Shunt.exe` anywhere — it's a single file, no install.
- Launch it and **say yes to the UAC prompt**. Without admin it still
  runs, but robocopy loses `/B` (backup mode) and ACL-protected folders
  on an attached drive will fail instead of copying.
- The window is dark with green text. Left side: the folder tree.
  Right side: destination and options. Bottom: live console + progress.

---

## Job 1 — Backup (pull data off)

**Source: attached old drive (most common)**

1. Plug the old drive into the dock/enclosure.
2. BACKUP tab → the **source** dropdown lists every drive that has a
   `Users` folder. Pick the old drive's letter.
3. Pick the **profile** (the customer's old username). The tree fills
   in — Desktop, Documents, Pictures, etc. are pre-ticked.
4. Expand **AppData (programs)** and **Discovered browsers (auto)** and
   tick what the customer needs — browser profiles are usually the point.
   Anything labelled *auto-discovered* was fingerprinted, not curated —
   glance at it before ticking.
5. **Destination:**
   - *folder* — pick an empty folder on your bench storage. Recommended
     when you'll restore to one machine.
   - *zip* — single portable archive (good for shelving or network
     moves). Stages through robocopy first, then compresses — slower
     overall.
6. **SPEED** (see "failing drives" below — healthy drive = `full`).
7. **START BACKUP** → `» RUNNING…`. The console streams robocopy's
   output; progress bar advances per item; status shows the file being
   copied.
8. When it says *done*, open `shunt_report.txt` (inside the destination)
   and eyeball the failed/skipped list before you call it finished.

**Source: live machine** — run Shunt on the machine itself, pick `C:`
as the source. Same flow. Files locked by running apps get skipped into
the report; close the customer's browsers first for a cleaner pull.

---

## Job 2 — Restore (shunt into the new profile)

1. On the **new machine** (or bench target), create the customer's
   Windows profile if it doesn't exist yet — log in once, or just pick
   the profile folder Shunt lists.
2. RESTORE tab → **backup** picker → choose the backup *folder* or the
   *.zip* → **inspect**. Never pick a bare drive letter (D:\ etc.) —
   that's a whole drive, not a backup; Shunt refuses it, but on old
   builds it would try to restore the entire drive into the profile.
3. Shunt finds `shunt_manifest.json` and lists what the backup contains.
   Tick what you want landed.
4. **target profile** dropdown → the new username. Paths are
   profile-relative, so old-`John` → new-`John Smith` just works.
5. **force overwrite** — leave off for a normal restore (identical files
   skip fast). Tick it if a previous run was interrupted and left
   half-copied files: it rewrites everything regardless.
6. Close browsers/Outlook on the target if the profile is in use —
   locked files fail into the report.
7. **START RESTORE**. Read `shunt_restore_report.txt` after.

---

## Failing drives (the Kingston special)

If the drive works briefly then freezes:

1. Set **SPEED** to `recovery` — ~1 MB/s, single-threaded, restartable.
   It'll take hours. That's the point: less I/O pressure, less heat.
2. Tick **auto-reset frozen drive**.
3. Tick the most valuable folders first — the tree is already ordered
   roughly importance-first (Documents, browsers before Videos/Music).
4. Start the job. Watch the console:
   - `no I/O for Ns` lines = the watchdog noticing the drive stall.
   - `resetting device` / `volume back online` = the dock re-enumerated
     and the copy resumed where it left off.
   - `reset failed` / volume never returns = the dock can't power-cycle
     it — repower the dock/enclosure physically, or hit **reset drive
     now** after a manual power cycle.
5. Each item gets 5 resets max, then it's failed into the report and the
   job moves on. A partial recovery is a good recovery — the report
   shows exactly what made it.

`gentle` (~6 MB/s) is the middle setting for drives that are flaky but
not yet freezing.

---

## Troubleshooting

| Symptom | What to do |
|---|---|
| Window gray / "not responding" | Shouldn't happen post-v1.0. If it does, enable **debug log** (top-right), reproduce, grab `Desktop\shunt_debug.log`. |
| Items failed in the report | Read the `!` lines — `ERROR 32` = file locked (close apps), `ERROR 5` = access denied (did you UAC?). |
| Stuck `shunt_staging_*` folder after a crashed zip backup | `tools\kill-staging.ps1` from the repo — run `powershell -ExecutionPolicy Bypass -File kill-staging.ps1 -Path "<path>"`. Safe to delete; it's a temp dir. |
| Restore refuses my backup folder | Only happens if it has no manifest *and* looks like a drive root (contains Windows/Program Files/Users…). Point it at the actual backup folder Shunt made — never at a drive letter itself. |
| Restore copied garbage / huge recursion (pre-1.2.3) | A drive root was used as the backup source. Delete the dumped folders from the profile (`kill-staging.ps1` helps with deep trees) and re-run against a real backup folder or zip. |
| Old `.ShuntBackup_*.staging` folder won't delete (pre-1.2.2) | Same script handles the dot-name + deep-path problem. |
| Browser passwords missing after restore | Expected — DPAPI. Export/import passwords separately. |
| Restore skipped files that should have copied | Tick **force overwrite** — the skip logic compares size+date and can pass over corrupt same-size files. |

## The debug toggle

`debug log` (top-right of the window) mirrors everything — engine
output, robocopy lines, watchdog events — to
`Desktop\shunt_debug.log`. If you're reporting a bug or a weird field
drive, turn it on *before* starting the job.
