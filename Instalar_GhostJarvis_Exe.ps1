# ============================================================
#  GHOST JARVIS - Instalador (version EJECUTABLE / sin Python)
#  Extrae GhostJarvis.exe (build congelado) + modelos + config,
#  crea accesos directos y registra autoarranque.
# ============================================================
$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
Clear-Host
$Gold="Yellow"; $White="White"; $Cyan="Cyan"; $Gray="Gray"; $Green="Green"; $Red="Red"

Write-Host ""
Write-Host "   ____ _   _  ___  ____ _____   _   _    _    ____  _   _ ___ ____  " -ForegroundColor $Cyan
Write-Host "  / ___| | | |/ _ \\/ ___|_   _| | | | |  / \\  |  _ \\| | | |_ _/ ___| " -ForegroundColor $Cyan
Write-Host " | |  _| |_| | | | \\___ \\ | |   | | | | / _ \\ | |_) | | | || |\\___ \\ " -ForegroundColor $Cyan
Write-Host " | |_| |  _  | |_| |___) || |   | |_| |/ ___ \\|  _ <| |_| || | ___) |" -ForegroundColor $Cyan
Write-Host "  \\____|_| |_|\\___/|____/ |_|    \\___//_/   \\_\\_| \\_\\\\___/|___|____/ " -ForegroundColor $Cyan
Write-Host ""
Write-Host "  ==============================================" -ForegroundColor $Gold
Write-Host "  INSTALADOR - VERSION EJECUTABLE (sin Python)" -ForegroundColor $Gold
Write-Host "  ==============================================" -ForegroundColor $Gold
Write-Host ""
Write-Host "Asistente de voz J.A.R.V.I.S. con overlay holografico." -ForegroundColor $White
Write-Host "Esta version NO requiere instalar Python: trae todo empaquetado." -ForegroundColor $White
Write-Host "(Si tu equipo no tiene GPU NVIDIA, el reconocimiento de voz corre en CPU.)" -ForegroundColor $Gray
Write-Host ""
Write-Host "Presiona ENTER para continuar o Ctrl+C para cancelar..." -ForegroundColor $Cyan -NoNewline
[void]$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
Write-Host "`n"

# --- Carpeta de instalacion ---
$DefaultInstallDir = Join-Path $env:LOCALAPPDATA "Programs\GhostJarvis"
Write-Host "Carpeta de instalacion (ENTER = por defecto):" -ForegroundColor $Gold
Write-Host "  $DefaultInstallDir" -ForegroundColor $Gray
$UserInput = Read-Host "Ruta"
$InstallDir = if ($UserInput.Trim() -ne "") { $UserInput.Trim() } else { $DefaultInstallDir }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
Write-Host "-> $InstallDir" -ForegroundColor $White
Write-Host ""

# --- Cerrar instancias activas ---
Write-Host "Cerrando instancias activas de Ghost Jarvis..." -ForegroundColor $Gray
Get-Process -Name "GhostJarvis" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# --- Localizar el zip ---
$ZipPath = Join-Path $PSScriptRoot "GhostJarvis-exe.zip"
if (-not (Test-Path -LiteralPath $ZipPath)) {
    Write-Host "ERROR: no se encontro GhostJarvis-exe.zip junto a este script." -ForegroundColor $Red
    Read-Host "ENTER para salir"; exit 1
}

# --- Extraer ---
Write-Host "Extrayendo aplicacion (puede tardar, son varios GB)..." -ForegroundColor $Gold
if (-not (Test-Path -LiteralPath $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
Expand-Archive -Path $ZipPath -DestinationPath $InstallDir -Force
Write-Host "OK - archivos extraidos." -ForegroundColor $Green
Write-Host ""

# --- Colocar configuracion del usuario (preserva tus ajustes; token en blanco) ---
$CfgDir = Join-Path $env:LOCALAPPDATA "GhostLabs\GhostJarvis"
$CfgDst = Join-Path $CfgDir "config.json"
$CfgSrc = Join-Path $InstallDir "config.migrado.json"
if (-not (Test-Path -LiteralPath $CfgDir)) { New-Item -ItemType Directory -Path $CfgDir -Force | Out-Null }
if (Test-Path -LiteralPath $CfgDst) {
    Write-Host "Ya existe una config previa en $CfgDst - se conserva (no se sobrescribe)." -ForegroundColor $Gray
} elseif (Test-Path -LiteralPath $CfgSrc) {
    Copy-Item -LiteralPath $CfgSrc -Destination $CfgDst -Force
    Write-Host "OK - configuracion migrada con tus ajustes (revisa el token mas abajo)." -ForegroundColor $Green
}
Write-Host ""

# --- Accesos directos (Escritorio + Menu Inicio + Inicio de Windows) ---
Write-Host "Creando accesos directos y autoarranque..." -ForegroundColor $Gold
$Exe = Join-Path $InstallDir "GhostJarvis.exe"
$ShellType = [Type]::GetTypeFromProgID("WScript.Shell")
$Shell = [Activator]::CreateInstance($ShellType)
function New-Lnk($path) {
    $s = $Shell.CreateShortcut($path)
    $s.TargetPath = $Exe
    $s.WorkingDirectory = $InstallDir
    $s.Description = "Ghost Jarvis - Asistente de voz"
    $s.Save()
}
try {
    New-Lnk (Join-Path ([System.Environment]::GetFolderPath("Desktop")) "Ghost Jarvis.lnk")
    $StartMenu = Join-Path ([System.Environment]::GetFolderPath("Programs")) "Ghost Jarvis"
    if (-not (Test-Path -LiteralPath $StartMenu)) { New-Item -ItemType Directory -Path $StartMenu -Force | Out-Null }
    New-Lnk (Join-Path $StartMenu "Ghost Jarvis.lnk")
    $Startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    New-Lnk (Join-Path $Startup "Ghost Jarvis.lnk")
    Write-Host "OK - Escritorio, Menu Inicio y autoarranque configurados." -ForegroundColor $Green
} catch {
    Write-Host "Aviso al crear accesos directos: $_" -ForegroundColor $Gold
}
Write-Host ""

# --- Recordatorio del token / OpenClaw ---
Write-Host "==============================================" -ForegroundColor $Gold
Write-Host "INSTALACION COMPLETADA" -ForegroundColor $Green
Write-Host "==============================================" -ForegroundColor $Gold
Write-Host ""
Write-Host "IMPORTANTE - para que Ghost responda en ESTE equipo:" -ForegroundColor $Cyan
Write-Host "  1. Instala y corre OpenClaw (el gateway) en este equipo." -ForegroundColor $White
Write-Host "  2. El token cifrado del equipo viejo NO sirve aqui (DPAPI por-usuario)." -ForegroundColor $White
Write-Host "     Abre Ghost Jarvis -> clic derecho en la bandeja -> Configuracion ->" -ForegroundColor $White
Write-Host "     pestana Conexion, y pega el 'gateway.auth.token' de tu" -ForegroundColor $White
Write-Host "     ~/.openclaw/openclaw.json de ESTE equipo. Reinicia la app." -ForegroundColor $White
Write-Host ""
$go = Read-Host "Abrir Ghost Jarvis ahora? (S/N)"
if ($go.Trim().ToUpper() -eq "S" -or $go.Trim() -eq "") {
    Start-Process -FilePath $Exe -WorkingDirectory $InstallDir
}
Write-Host "Listo." -ForegroundColor $Gold
Start-Sleep -Seconds 2
