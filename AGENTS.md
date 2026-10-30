# Shunt — agent notes

Repo: https://github.com/gcnewbief/Shunt (branch: master, remote: origin).
`gh` is keyring-authenticated and configured as git's credential helper.

Windows data-migration tool for a repair-shop workflow. Pulls user
profiles off old drives (or a live machine) into a folder/zip backup,
restores them into a new profile. Python + CustomTkinter GUI over a
per-item `robocopy` engine. Dark/green "terminal" aesthetic.

## Commands

- Run from source: `py shunt_app.py` (or `py -m shunt`)
- Build exe: `py build.py` → `dist\Shunt.exe` (one-file, `--uac-admin`,
  icon+assets bundled)
- Engine tests: `py tests/smoke_engine.py` — self-contained; builds
  `tests/mock_drive/` with a junction loop + denied-ACL dir, runs
  backup→zip→restore round-trip, asserts 17 checks
- Regenerate icon/logo: `py tools/make_icon.py` → `assets/shunt.{png,ico}`
- GUI checks: there is no GUI test harness — instantiate `ShuntApp`,
  call `app.update()`, assert `winfo_ismapped()` on panels/widgets.

`py` is the interpreter (`python`/`python3` are not on PATH).

## Architecture

```
shunt/
  app.py            CTk window, tabs, event plumbing, debug-log toggle
  elevate.py        IsUserAnAdmin + runas relaunch (enables robocopy /B)
  core/
    copier.py       build_args / run_item — robocopy runner + I/O watchdog
    disk.py         failing-drive support — PNP id lookup, pnputil reset
    catalog.py      CatalogItem tree — WHAT to copy, profile-root-relative
    profiles.py     find/look-like profile dirs; drive_candidates()
    manifest.py     shunt_manifest.json in every backup
    backup.py       run_backup — SPEED_PRESETS, zip staging, reset-retry
    restore.py      inspect_backup / run_restore (folder=robocopy, zip=extract)
  ui/
    theme.py        color/font constants (GREEN=#3dff73 etc.)
    tree.py         CheckTree — [ ]/[x]/[-] glyph checkboxes
    tooltip.py      hover tooltips
tools/make_icon.py  draws the bulldozer mascot
tests/smoke_engine.py
```

UI↔engine contract: worker threads emit tuples on a `queue.Queue`;
`_JobPanel._drain` consumes on the UI thread. Kinds: `log`, `file`,
`item_start(i,n,label)`, `item_done(label,ItemResult)`,
`zip_progress(i,total)`, `phase(text)`, `inspect`, `inspect_err`,
`done(report_path)`.

## Robocopy rationale (copier.py docstring has detail)

`/B` = backup mode — bypasses ACL negotiation when elevated (the whole
point vs Explorer). `/COPY:DAT` deliberately skips ACLs — old-drive SIDs
are meaningless on the new machine. `/XJ` junctions, `/XA:O` OneDrive
placeholders, `/R:1 /W:1` locked files fail fast into the report,
`/MT:16` threads. Exit codes are bitflags: <8 = OK.

## Hard-won pitfalls — don't reintroduce

- **Pack every panel.** A CTkFrame child of a tabview that's never
  `pack()`ed renders a blank window but constructs fine — always verify
  with `winfo_ismapped()` in smoke checks.
- **Catalog containers have `rel_path=None` AND no payload.** A
  payloaded container both crashes (`Path / None`) and suppresses its
  children in `get_checked`. `run_backup` also filters defensively.
- **Never block the UI thread.** The drain loop is time-bounded (25ms)
  precisely because an unbounded `get_nowait()` loop starves the
  mainloop under robocopy output floods → gray "Not Responding" window
  while the worker keeps copying. Log floods collapse with a skip
  counter; `file` events throttled to 10/s at the source;
  `inspect_backup` runs in a thread (zip `namelist()` is slow).
- **Glyph safety:** stick to cp1252-safe glyphs in button/label text —
  `⟳`/`✓` may not render; `»`, `·`, `✕` are safe.
- **Zip mode stages through robocopy first** so `/B` still applies —
  don't "optimize" that into pure-Python reads or ACL-denied drives
  will fail. Leftover `shunt_staging_*` dirs = interrupted zip backups,
  safe to delete — if a stale one resists deletion (deep paths inside),
  use `rd /s /q "\\?\X:\path\dir"`. `_nuke_dir` handles it in code.
  Never prefix staging names with a dot — Explorer/cmd choke on them.
- **Zip staging skips offline-attribute files.** `/B` can carry OneDrive
  reparse stubs into staging where `zipfile` then dies with
  ERROR_CANT_ACCESS_FILE. `_zip_dir` skips them by attribute and catches
  per-file OSError into the report — never let one file kill a zip.
- **Freeze detection watches I/O counters, not output.** A huge file
  produces no stdout for minutes while slowly copying — silence ≠ hung.
  `copier._proc_read_bytes` (GetProcessIoCounters) distinguishes frozen
  drives from slow ones; watchdog kills only on zero-I/O for STALL_AFTER
  (180s). Recovery mode adds `/ZB` + `/IPG:50`; `disk.reset_drive` does
  pnputil disable/enable by drive letter and refuses the system volume.
- **robocopy skips = feature.** Identical files (size+timestamp) skip
  on re-runs — that's incremental backup, not a block. "force
  overwrite" (restore only) adds `/IS` / disables zip same-size skip.
- **Manifest-less restore sources are guarded.** Real incident: pointing
  restore at `D:\` (old OS drive) enumerated every root dir as an item
  and dumped ~1.8TB incl. junction loops into the target profile.
  `inspect_backup` refuses drive roots and dirs matching
  `_DRIVE_ROOT_HINTS` without a manifest; `run_restore` re-checks,
  refuses source/dest overlap, and skips reparse-point item sources
  (`lstat` — `stat()` follows the link and misses it). `/XJ` does NOT
  cover dir symlinks or a junction used as the *source root*, so
  `run_item` also kills any copy whose paths exceed `max_rel_depth`
  (40) — that's the loop canary. `tools/kill-staging.ps1` force-deletes
  the fallout in the field.
  `ERROR 32` in a report = destination file locked by a running app.

## Cutting a release

1. Bump `shunt/__init__.py:__version__`, `py build.py`, commit.
2. Tag + release (git has no global identity — use `-c` flags):

   ```
   git -c user.name="Devin" -c user.email="158243242+devin-ai-integration[bot]@users.noreply.github.com" tag -a vX.Y.Z -m "Shunt vX.Y.Z"
   git push origin master vX.Y.Z
   gh release create vX.Y.Z dist/Shunt.exe --repo gcnewbief/Shunt --title "Shunt vX.Y.Z" --notes "…"
   ```

## Known limits (by design, v1.1)

- Browser passwords don't migrate — DPAPI per-user encryption. Export
  on old machine; README explains.
- No VSS — files locked on a *running* source are skipped to report.
- `ntuser.dat*`, `*.ost`, caches, junctions are excluded on purpose.

## Debugging in the field

Header `debug log` checkbox → streams all engine output to
`Desktop\shunt_debug.log`. Every run also writes `shunt_report.txt`
(backups, next to dest) / `shunt_restore_report.txt` (into profile).
