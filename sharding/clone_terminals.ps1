# clone_terminals.ps1
# ---------------------------------------------------------------
# Makes one MT5 terminal folder per shard.
#
# Each worker process needs its OWN terminal. They cannot share one
# — that's the whole point of sharding.
#
# Run from PowerShell:
#     .\clone_terminals.ps1 -Count 2
# ---------------------------------------------------------------

param(
    [int]$Count = 2,
    [string]$Source = "C:\Users\user\Desktop\TraderMT5",
    [string]$Dest   = "C:\MT5"
)

Write-Host ""
Write-Host "  Cloning $Source -> $Count shard folders under $Dest" -ForegroundColor Cyan
Write-Host ""

# --- Safety checks --------------------------------------------------------

if (-not (Test-Path "$Source\terminal64.exe")) {
    Write-Host "  ERROR: no terminal64.exe found at $Source" -ForegroundColor Red
    Write-Host "  Edit the -Source path to point at your working MT5 folder."
    exit 1
}

$running = Get-Process terminal64 -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "  ERROR: MT5 is currently running. Close every terminal first," -ForegroundColor Red
    Write-Host "  otherwise you'll copy locked/half-written files."
    exit 1
}

# Rough size warning — each clone is a full copy.
$sizeGB = [math]::Round((Get-ChildItem $Source -Recurse -ErrorAction SilentlyContinue |
           Measure-Object -Property Length -Sum).Sum / 1GB, 2)
Write-Host "  Source folder is ~$sizeGB GB. $Count clones = ~$([math]::Round($sizeGB * $Count,2)) GB of disk."
Write-Host ""

# --- Clone ----------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

for ($i = 0; $i -lt $Count; $i++) {
    $target = Join-Path $Dest "shard$i"

    if (Test-Path $target) {
        Write-Host "  shard$i already exists — skipping" -ForegroundColor Yellow
        continue
    }

    Write-Host "  creating shard$i ..." -NoNewline
    Copy-Item -Path $Source -Destination $target -Recurse -Force

    # Trim junk the clone doesn't need. Each terminal rebuilds its own.
    foreach ($junk in @("logs", "MQL5\Logs", "Tester")) {
        $p = Join-Path $target $junk
        if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }
    }

    Write-Host " done" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Finished. Your shard terminals:" -ForegroundColor Green
for ($i = 0; $i -lt $Count; $i++) {
    Write-Host "    $Dest\shard$i\terminal64.exe"
}
Write-Host ""
Write-Host "  Next: run start_shards.bat" -ForegroundColor Cyan
Write-Host ""
