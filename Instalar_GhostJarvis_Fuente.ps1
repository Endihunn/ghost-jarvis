# ============================================================
#  GHOST JARVIS - Instalador (version FUENTE / Python)
#  Extrae el codigo, crea un entorno virtual, instala las
#  dependencias (incluye torch CUDA), coloca tu config y
#  registra el autoarranque.
#  Requiere: Python 3.10-3.13 instalado (probado en 3.13).
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
Write-Host "  INSTALADOR - VERSION FUENTE (Python + venv)" -ForegroundColor $Gold
Write-Host "  ==============================================" -ForegroundColor $Gold
Write-Host ""

# --- Verificar Python ---
Write-Host "Buscando Python..." -ForegroundColor $Gray
$Py = $null
foreach ($cmd in @("py -3", "python", "python3")) {
    try {
        $parts = $cmd.Split(" ")
        $v = & $parts[0] $parts[1..($parts.Length-1)] --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { $Py = $cmd; Write-Host "  Encontrado: $v ($cmd)" -ForegroundColor $Green; break }
    } catch {}
}
if (-not $Py) {
    Write-Host "ERROR: no encontre Python. Instala Python 3.13 desde https://www.python.org/downloads/" -ForegroundColor $Red
    Write-Host "       (marca 'Add python.exe to PATH') y vuelve a ejecutar este instalador." -ForegroundColor $Red
    Read-Host "ENTER para salir"; exit 1
}
Write-Host ""

# --- Carpeta de instalacion ---
$DefaultInstallDir = Join-Path $env:USERPROFILE "ghost-jarvis"
Write-Host "Carpeta de instalacion (ENTER = por defecto):" -ForegroundColor $Gold
Write-Host "  $DefaultInstallDir" -ForegroundColor $Gray
$UserInput = Read-Host "Ruta"
$InstallDir = if ($UserInput.Trim() -ne "") { $UserInput.Trim() } else { $DefaultInstallDir }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
Write-Host "-> $InstallDir" -ForegroundColor $White
Write-Host ""

# --- Localizar y extraer el zip ---
$ZipPath = Join-Path $PSScriptRoot "GhostJarvis-fuente.zip"
if (-not (Test-Path -LiteralPath $ZipPath)) {
    Write-Host "ERROR: no se encontro GhostJarvis-fuente.zip junto a este script." -ForegroundColor $Red
    Read-Host "ENTER para salir"; exit 1
}
Write-Host "Extrayendo codigo y modelos..." -ForegroundColor $Gold
if (-not (Test-Path -LiteralPath $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
Expand-Archive -Path $ZipPath -DestinationPath $InstallDir -Force
Write-Host "OK - extraido." -ForegroundColor $Green
Write-Host ""

# --- Crear entorno virtual ---
Push-Location $InstallDir
$parts = $Py.Split(" ")
Write-Host "Creando entorno virtual (.venv)..." -ForegroundColor $Gold
& $parts[0] $parts[1..($parts.Length-1)] -m venv .venv
$VenvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
Write-Host "Actualizando pip..." -ForegroundColor $Gray
& $VenvPy -m pip install --upgrade pip --quiet

# --- Instalar torch (CUDA) y dependencias ---
Write-Host "Instalando PyTorch CUDA (cu126). Esto descarga ~2.5 GB, paciencia..." -ForegroundColor $Gold
& $VenvPy -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
if ($LASTEXITCODE -ne 0) {
    Write-Host "Aviso: fallo torch CUDA. Intentando torch CPU..." -ForegroundColor $Gold
    & $VenvPy -m pip install torch torchaudio
    Write-Host "Nota: sin CUDA, pon \"gpu_enabled\": false en la config para evitar errores." -ForegroundColor $Gray
}
Write-Host "Instalando el resto de dependencias (requirements.txt)..." -ForegroundColor $Gold
& $VenvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Aviso: pip fallo. Si usas ESET/antivirus, puede estar bloqueando pip." -ForegroundColor $Gold
    Write-Host "Reintenta con la red activa o agrega una exclusion temporal y corre:" -ForegroundColor $Gray
    Write-Host "  $VenvPy -m pip install -r requirements.txt" -ForegroundColor $Gray
}
Write-Host ""

# --- Colocar configuracion (preserva tus ajustes; token en blanco) ---
$CfgDir = Join-Path $env:LOCALAPPDATA "GhostLabs\GhostJarvis"
$CfgDst = Join-Path $CfgDir "config.json"
$CfgSrc = Join-Path $InstallDir "config.migrado.json"
if (-not (Test-Path -LiteralPath $CfgDir)) { New-Item -ItemType Directory -Path $CfgDir -Force | Out-Null }
if (Test-Path -LiteralPath $CfgDst) {
    Write-Host "Ya existe config previa - se conserva (no se sobrescribe)." -ForegroundColor $Gray
} elseif (Test-Path -LiteralPath $CfgSrc) {
    Copy-Item -LiteralPath $CfgSrc -Destination $CfgDst -Force
    Write-Host "OK - configuracion migrada con tus ajustes." -ForegroundColor $Green
}

# --- Registrar autoarranque + acceso directo (usa la logica de la propia app) ---
Write-Host "Registrando autoarranque y acceso directo..." -ForegroundColor $Gold
try {
    & $VenvPy startup_installer.py
    $ShellType = [Type]::GetTypeFromProgID("WScript.Shell")
    $Shell = [Activator]::CreateInstance($ShellType)
    $lnk = $Shell.CreateShortcut((Join-Path ([System.Environment]::GetFolderPath("Desktop")) "Ghost Jarvis.lnk"))
    $lnk.TargetPath = Join-Path $InstallDir "launch.vbs"
    $lnk.WorkingDirectory = $InstallDir
    $lnk.Description = "Ghost Jarvis - Asistente de voz"
    $lnk.Save()
    Write-Host "OK - autoarranque + acceso directo en el Escritorio." -ForegroundColor $Green
} catch {
    Write-Host "Aviso al registrar autoarranque: $_" -ForegroundColor $Gold
}
Pop-Location
Write-Host ""

Write-Host "==============================================" -ForegroundColor $Gold
Write-Host "INSTALACION COMPLETADA" -ForegroundColor $Green
Write-Host "==============================================" -ForegroundColor $Gold
Write-Host ""
Write-Host "IMPORTANTE - para que Ghost responda en ESTE equipo:" -ForegroundColor $Cyan
Write-Host "  1. Instala OpenClaw:  npm install -g openclaw   y corre el gateway." -ForegroundColor $White
Write-Host "  2. El token del equipo viejo NO sirve aqui (cifrado DPAPI por-usuario)." -ForegroundColor $White
Write-Host "     Abre Ghost Jarvis -> bandeja -> Configuracion -> Conexion y pega el" -ForegroundColor $White
Write-Host "     'gateway.auth.token' del ~/.openclaw/openclaw.json de ESTE equipo." -ForegroundColor $White
Write-Host ""
$go = Read-Host "Abrir Ghost Jarvis ahora? (S/N)"
if ($go.Trim().ToUpper() -eq "S" -or $go.Trim() -eq "") {
    Start-Process -FilePath (Join-Path $InstallDir "launch.vbs")
}
Write-Host "Listo." -ForegroundColor $Gold
Start-Sleep -Seconds 2
