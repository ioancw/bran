# Pull one complete backup of bran from the server to this laptop.
#
#   pwsh -File scripts\pull_backup.ps1 -Server bran@<server address>
#
# Sends scripts/backup_stream.sh to the server over ssh and runs it there (so
# the server needs nothing installed for this), saves the archive as
# <Dest>\bran-<date>-<time>.tar.gz, checks it can be read and holds the
# database, then keeps the newest <Keep> archives and deletes older ones.
# Every run adds a line to <Dest>\backup.log. Exit code 0 means a good backup.
#
# Meant to be run daily by a Windows scheduled task (see USAGE.md). To restore:
# stop bran, unpack the archive, put bran.sqlite and the folders back under
# the data directory and .claude/projects back under the home directory.
param(
    [Parameter(Mandatory)] [string] $Server,
    [string] $Dest = (Join-Path $HOME 'bran-backups'),
    [int] $Keep = 14
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Dest | Out-Null
$log = Join-Path $Dest 'backup.log'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$part = Join-Path $Dest "bran-$stamp.part"
$final = Join-Path $Dest "bran-$stamp.tar.gz"
$remote = Join-Path $PSScriptRoot 'backup_stream.sh'

function Write-Log([string] $text) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $text" | Add-Content -Path $log -Encoding utf8
    Write-Host $text
}

function Stop-Backup([string] $why) {
    Remove-Item $part -ErrorAction SilentlyContinue
    Write-Log "FAILED: $why"
    exit 1
}

# cmd does the redirect: it writes the bytes exactly as they arrive.
$err = Join-Path $Dest 'last-error.txt'
cmd /c "ssh -o BatchMode=yes -o ConnectTimeout=20 $Server bash -s < `"$remote`" > `"$part`" 2> `"$err`""
if ($LASTEXITCODE -ne 0) {
    $detail = (Get-Content $err -Raw -ErrorAction SilentlyContinue) -replace '\s+', ' '
    Stop-Backup "the server did not send a backup (exit $LASTEXITCODE). $detail"
}

$names = tar -tzf $part 2>$null
if ($LASTEXITCODE -ne 0) { Stop-Backup 'the archive that arrived cannot be read' }
if ($names -notcontains 'bran.sqlite') { Stop-Backup 'the archive has no database in it' }

Move-Item $part $final
Remove-Item $err -ErrorAction SilentlyContinue
$mb = [math]::Round((Get-Item $final).Length / 1MB, 1)

$old = Get-ChildItem $Dest -Filter 'bran-*.tar.gz' | Sort-Object Name -Descending | Select-Object -Skip $Keep
$old | Remove-Item
Write-Log "ok: $(Split-Path $final -Leaf), $mb MB, $($names.Count) entries, removed $(@($old).Count) old"
