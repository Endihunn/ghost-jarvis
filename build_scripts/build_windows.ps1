# Build script for Windows MSI installer
# Requires: pyinstaller, WiX Toolset v4 (wix.exe in PATH)
param(
    [string]$Version = "1.0.0",
    [string]$OutputDir = "$PSScriptRoot\..\dist"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\.."
$Dist = "$OutputDir\GhostJarvis"
$MsiOut = "$OutputDir\GhostJarvis-$Version.msi"

Write-Host "=== Ghost Jarvis Windows Build ==="
Write-Host "Version: $Version"
Write-Host "Root:    $Root"

# 1. PyInstaller bundle
Write-Host "`n[1/4] Running PyInstaller..."
pyinstaller "$Root\build_scripts\common\ghost_jarvis.spec" --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 2. Ensure VC++ redists are present (optional but recommended)
# If redists are needed, copy them here.

# 3. WiX v4 build
Write-Host "`n[2/4] Building MSI with WiX v4..."
$Wxs = "$Root\build_scripts\wix\ghost_jarvis.wxs"
if (-not (Test-Path $Wxs)) {
    Write-Warning "WiX source not found at $Wxs — skipping MSI"
    exit 0
}

wix build -arch x64 -out "$MsiOut" -d Version=$Version $Wxs
if ($LASTEXITCODE -ne 0) { throw "WiX build failed" }

Write-Host "`n[3/4] Done. MSI: $MsiOut"
