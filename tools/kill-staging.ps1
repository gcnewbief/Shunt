<#
.SYNOPSIS
    Force-delete a stuck Shunt staging folder (deep paths, weird names).

.DESCRIPTION
    Escalates through every deletion technique that matters on Windows:
    1. takeown/icacls + attribute strip (deny-ACLs, read-only/system attrs)
    2. move to C:\_shunt_del — shortens every child path in one operation
    3. robocopy empty-mirror (native long-path + junction-safe delete)
    4. .NET Directory.Delete on the \\?\ extended path
    5. cmd rd /s /q on the \\?\ path
    6. 8.3 short-name rd
    Then reports anything that survived.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File kill-staging.ps1
    powershell -ExecutionPolicy Bypass -File kill-staging.ps1 -Path "D:\shunt_staging_foo"
#>
param(
    [string]$Path = "C:\Users\userpc\Desktop\.ShuntBackup_userpc_20261027.staging"
)

$ErrorActionPreference = 'Continue'

function Write-Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Show-Survivors($target) {
    $ext = "\\?\$target"
    $left = Get-ChildItem -LiteralPath $ext -Recurse -Force -ErrorAction SilentlyContinue
    if ($left) {
        Write-Host "`n$($left.Count) item(s) remain:" -ForegroundColor Yellow
        $left | Select-Object -First 15 | ForEach-Object { Write-Host "  $($_.FullName)" }
        if ($left.Count -gt 15) { Write-Host "  ... and $($left.Count - 15) more" }
    }
}

$full = [System.IO.Path]::GetFullPath($Path.TrimEnd('\'))
$ext  = "\\?\$full"

if (-not (Test-Path -LiteralPath $ext) -and -not (Test-Path -LiteralPath $full)) {
    Write-Host "Nothing at $full - already gone." -ForegroundColor Green
    exit 0
}
Write-Host "Target: $full"

# --- 1: ownership, ACLs, attributes -------------------------------
Write-Step "1/6 takeown + icacls + attribute strip"
takeown /f "$full" /r /d y 2>&1 | Out-Null
icacls "$full" /grant "*S-1-5-32-544:F" /t /c /q 2>&1 | Out-Null
Get-ChildItem -LiteralPath $ext -Recurse -Force -ErrorAction SilentlyContinue |
    ForEach-Object { try { $_.Attributes = 'Normal' } catch {} }

# --- 2: move to drive root - shrinks every child path -------------
Write-Step "2/6 relocate to drive root (path shortening)"
$drive = [System.IO.Path]::GetPathRoot($full)
$short = Join-Path $drive "_shunt_del"
if (Test-Path -LiteralPath $short) { Remove-Item -LiteralPath $short -Recurse -Force -ErrorAction SilentlyContinue }
try {
    [System.IO.Directory]::Move($ext, "\\?\$short")
    Write-Host "  moved -> $short"
    $full = $short; $ext = "\\?\$short"
} catch {
    Write-Host "  move failed ($($_.Exception.Message)) - continuing in place"
}
$target = $full

# --- 3: robocopy empty-mirror --------------------------------------
Write-Step "3/6 robocopy empty-mirror"
$empty = Join-Path $env:TEMP "shunt_empty"
New-Item -ItemType Directory -Path $empty -Force | Out-Null
robocopy "$empty" "$target" /MIR /NFL /NDL /NJH /NJS /NP /R:0 /W:0 | Out-Null
Remove-Item -LiteralPath $empty -Recurse -Force -ErrorAction SilentlyContinue
cmd /c "rd /s /q `"$target`"" 2>$null | Out-Null

# --- 4: .NET delete on extended path --------------------------------
if (Test-Path -LiteralPath $target) {
    Write-Step "4/6 .NET Directory.Delete (\\?\)"
    try { [System.IO.Directory]::Delete($ext, $true) }
    catch { Write-Host "  $($_.Exception.Message)" -ForegroundColor DarkYellow }
}

# --- 5: cmd rd on extended path --------------------------------------
if (Test-Path -LiteralPath $target) {
    Write-Step "5/6 cmd rd /s /q (\\?\)"
    cmd /c "rd /s /q `"$ext`""
}

# --- 6: 8.3 short name -------------------------------------------------
if (Test-Path -LiteralPath $target) {
    Write-Step "6/6 8.3 short name"
    try {
        $sn = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($target).ShortPath
        Write-Host "  short: $sn"
        cmd /c "rd /s /q `"$sn`""
    } catch { Write-Host "  no short name available" }
}

# --- verdict -------------------------------------------------------------
if (Test-Path -LiteralPath $target) {
    Write-Host "`nFAILED - $target survived everything." -ForegroundColor Red
    Show-Survivors $target
    Write-Host @"

Next options:
  * open handle held? -> reboot the machine, run again before opening anything
  * NTFS corruption? -> chkdsk $drive /f  (schedule, then reboot)
  * still stuck?     -> Sysinternals 'handle64 -a staging' to find the owner
"@
    exit 1
}
Write-Host "`nDELETED - $target is gone." -ForegroundColor Green
exit 0
