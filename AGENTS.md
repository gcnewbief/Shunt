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
    copier.py       build_args / run_item — the robocopy subprocess runner
    catalog.py      CatalogItem tree — WHAT to copy, profile-root-relative
    profiles.py     find/look-like profile dirs; drive_candidates()
    manifest.py     shunt_manifest.json in every backup
    backup.py       run_backup: folder direct, zip via staging dir
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
  will fail. Leftover `.ShuntBackup_*.staging` dirs = interrupted zip
  backups, safe to delete.
- **Freeze detection watches I/O counters, not output.** A huge file
  produces no stdout for minutes while slowly copying — silence ≠ hung.
  `copier._proc_read_bytes` (GetProcessIoCounters) distinguishes frozen
  drives from slow ones; watchdog kills only on zero-I/O for STALL_AFTER
  (180s). Recovery mode adds `/ZB` + `/IPG:50`; `disk.reset_drive` does
  pnputil disable/enable by drive letter and refuses the system volume.
- **robocopy skips = feature.** Identical files (size+timestamp) skip
  on re-runs — that's incremental backup, not a block. "force
  overwrite" (restore only) adds `/IS` / disables zip same-size skip.
  `ERROR 32` in a report = destination file locked by a running app.

## Known limits (by design, v1.0)

- Browser passwords don't migrate — DPAPI per-user encryption. Export
  on old machine; README explains.
- No VSS — files locked on a *running* source are skipped to report.
- `ntuser.dat*`, `*.ost`, caches, junctions are excluded on purpose.

## Debugging in the field

Header `debug log` checkbox → streams all engine output to
`Desktop\shunt_debug.log`. Every run also writes `shunt_report.txt`
(backups, next to dest) / `shunt_restore_report.txt` (into profile).
