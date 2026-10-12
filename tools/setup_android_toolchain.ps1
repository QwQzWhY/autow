# Downloads a self-contained Android build toolchain into %LOCALAPPDATA%,
# outside the project tree. ASCII only on purpose: Windows PowerShell 5.1 reads
# .ps1 files as ANSI unless they carry a BOM, and non-ASCII source breaks the parser.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Join-Path $env:LOCALAPPDATA 'autow-toolchain'
New-Item -ItemType Directory -Force -Path $root | Out-Null

function Fetch([string]$url, [string]$out) {
    if (Test-Path -LiteralPath $out) { Write-Host "have $out"; return }
    Write-Host "fetch $url"
    & curl.exe -sS -L --max-time 1800 -o $out $url
    Write-Host ("done {0} ({1:N0} MB)" -f $out, ((Get-Item -LiteralPath $out).Length / 1MB))
}

Fetch 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse' (Join-Path $root 'jdk17.zip')
Fetch 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip' (Join-Path $root 'cmdline-tools.zip')
Fetch 'https://services.gradle.org/distributions/gradle-8.7-bin.zip' (Join-Path $root 'gradle.zip')

Write-Host 'extracting'
foreach ($pair in @(@('jdk17.zip', 'jdk'), @('gradle.zip', 'gradle'), @('cmdline-tools.zip', 'sdk-tmp'))) {
    $zip = Join-Path $root $pair[0]
    $dest = Join-Path $root $pair[1]
    if (Test-Path -LiteralPath $dest) { Write-Host "have $dest"; continue }
    Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
}

# sdkmanager requires the layout <sdk>/cmdline-tools/latest/bin
$latest = Join-Path $root 'sdk\cmdline-tools\latest'
if (-not (Test-Path -LiteralPath $latest)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $latest) | Out-Null
    Move-Item -LiteralPath (Join-Path $root 'sdk-tmp\cmdline-tools') -Destination $latest
}

Write-Host 'toolchain ready'
Get-ChildItem $root -Directory | ForEach-Object { Write-Host " " $_.Name }
