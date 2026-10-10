<#  write-usb.ps1 — write the kAIrOS ISO to a USB stick (Windows, raw).
    DESTRUCTIVE: erases the target physical disk.
    Usage:  pwsh -File write-usb.ps1 -DiskNumber <N> [-Iso <path>]
    Find <N> with:  Get-Disk | ? BusType -eq 'USB'
    The ISO defaults to the WSL build output via the \\wsl.localhost path.
#>
param(
    [Parameter(Mandatory = $true)][int]$DiskNumber,
    [string]$Iso = "\\wsl.localhost\Debian\srv\kairos-out\kairos-hwtest-4070.iso",
    [switch]$Yes
)
$ErrorActionPreference = "Stop"

$disk = Get-Disk -Number $DiskNumber
if ($disk.BusType -ne 'USB') {
    Write-Warning "Disk $DiskNumber is $($disk.BusType), NOT USB. This is almost certainly the wrong target."
}
if (-not (Test-Path $Iso)) { throw "ISO not found: $Iso" }
$isoSize = (Get-Item $Iso).Length

Write-Host "TARGET DISK $DiskNumber :" -ForegroundColor Yellow
$disk | Format-List Number, FriendlyName, BusType, @{N='SizeGB';E={[math]::Round($_.Size/1GB,1)}}, OperationalStatus
Write-Host ("ISO: {0}  ({1:N1} GB)" -f $Iso, ($isoSize/1GB))
if ($disk.Size -lt $isoSize) { throw "Disk too small for this ISO." }

if (-not $Yes) {
    $c = Read-Host "This ERASES disk $DiskNumber completely. Type ERASE to proceed"
    if ($c -ne 'ERASE') { Write-Host "aborted."; exit 1 }
}

Write-Host "clearing partition table…"
Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue
Get-Disk -Number $DiskNumber | Set-Disk -IsOffline $false -ErrorAction SilentlyContinue

# raw stream the ISO to \\.\PhysicalDriveN
$path = "\\.\PhysicalDrive$DiskNumber"
Write-Host "writing raw to $path (this takes a while)…"
$src = [System.IO.File]::OpenRead($Iso)
$dst = New-Object System.IO.FileStream($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $buf = New-Object byte[] (8MB)
    [long]$tot = 0; $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) {
        $dst.Write($buf, 0, $n); $tot += $n
        if ($sw.Elapsed.TotalSeconds -ge 3) {
            Write-Progress -Activity "Writing kAIrOS USB" -Status ("{0:N1} / {1:N1} GB" -f ($tot/1GB), ($isoSize/1GB)) -PercentComplete ([math]::Min(100, $tot*100/$isoSize))
            $sw.Restart()
        }
    }
    $dst.Flush()
} finally { $dst.Close(); $src.Close() }
Write-Progress -Activity "Writing kAIrOS USB" -Completed
Write-Host "DONE — $([math]::Round($tot/1GB,1)) GB written. Eject and boot the laptop from this stick." -ForegroundColor Green
