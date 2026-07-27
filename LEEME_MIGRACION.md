# Ghost Jarvis — Paquete de migración

Respaldo para **replicar la instalación de Ghost Jarvis en otro equipo**.
Generado el 2026-06-14 desde `C:\Users\darth\ghost-jarvis` (la instancia que corre en la bandeja).

Ghost Jarvis es un asistente de voz de escritorio (Python 3.13 + PyQt6): overlay
holográfico OpenGL, STT con `faster-whisper` (GPU/CUDA), TTS `edge-tts` con efectos
J.A.R.V.I.S., y un puente WebSocket al gateway local de **OpenClaw**.

---

## Contenido de esta carpeta

| Archivo | Qué es | Tamaño aprox. |
|---|---|---|
| `GhostJarvis-fuente.zip` | Código + modelos Whisper + tu config (sin secretos). Se instala con Python. | ~1.8 GB |
| `Instalar_GhostJarvis_Fuente.ps1` | Instalador de la versión fuente (crea venv, instala deps, autoarranque). | — |
| `GhostJarvis-exe.zip` | Build congelado `GhostJarvis.exe` (PyInstaller) + modelos + tu config. **No requiere Python.** | ~8 GB |
| `Instalar_GhostJarvis_Exe.ps1` | Instalador de la versión ejecutable (extrae, accesos directos, autoarranque). | — |
| `LEEME_MIGRACION.md` | Este archivo. | — |

Elige **una** de las dos vías de instalación (no necesitas ambas).

---

## Cuál elegir

- **Versión EJECUTABLE** (`...-exe.zip`) → la más simple. No instalas nada de Python.
  Ideal si el otro equipo no es para desarrollo. Pesa más y el soporte de GPU quedó
  "horneado" para CUDA; en un equipo sin NVIDIA corre el STT en CPU (más lento pero
  funciona).
- **Versión FUENTE** (`...-fuente.zip`) → más ligera y flexible. Requiere **Python
  3.10–3.13**. pip resuelve las librerías correctas para ese equipo (incluida torch).
  Recomendada si quieres seguir editando el código o tener el `.git`.

---

## Cómo instalar

1. Copia a una carpeta local del equipo nuevo **el `.zip` que elijas + su `Instalar_*.ps1`**
   (que queden juntos).
2. Clic derecho en el `.ps1` → **Ejecutar con PowerShell**.
   - Si Windows bloquea el script: abre PowerShell y corre
     `Set-ExecutionPolicy -Scope Process Bypass` y luego `.\Instalar_GhostJarvis_Exe.ps1`.
3. Sigue el asistente (carpeta destino, accesos directos, abrir al terminar).

---

## ⚠️ Paso obligatorio: el token NO se migra

Los secretos (`gateway_token`, `session_key`) se cifran con **DPAPI por-usuario de
Windows**, así que **no se pueden descifrar en otro equipo ni en otra cuenta**. Por eso
tu config se migra con todos tus ajustes **pero con esos dos campos en blanco**.

Para dejar a Ghost respondiendo en el equipo nuevo:

1. Instala y corre **OpenClaw** ahí: `npm install -g openclaw` y levanta el gateway
   (`openclaw gateway`).
2. Abre `~/.openclaw/openclaw.json` de **ese** equipo y copia `gateway.auth.token`.
3. En Ghost Jarvis: clic derecho en la bandeja → **Configuración → Conexión** → pega el
   token → guarda → **reinicia la app**.

> No edites el `config.json` a mano con el Bloc de notas / `Set-Content`: si le mete BOM
> UTF-8, la app no lo carga. Usa el diálogo de configuración de la app.

Ubicación de la config en el equipo nuevo:
`%LOCALAPPDATA%\GhostLabs\GhostJarvis\config.json`

---

## Qué NO se incluyó (a propósito)

Se omitió ~15 GB de cosas regenerables: el entorno virtual `.venv` (no es portable
entre equipos), las carpetas `build/` y `dist/` de PyInstaller, logs y cachés de TTS.
Se conservaron: código, documentación, `.git` (en la versión fuente), `assets/sounds`,
los modelos Whisper y tu configuración.

---

## Problemas comunes

- **"pip falló" en la instalación fuente** → suele ser ESET/antivirus bloqueando pip.
  Reintenta con red activa o agrega una exclusión temporal y corre de nuevo:
  `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
- **torch no instala con CUDA** → el instalador cae solo a torch CPU; pon
  `"gpu_enabled": false` en la config si el equipo no tiene NVIDIA.
- **No se ven los cubos** → la GPU no soporta OpenGL 3.3+; aparecerá un círculo cyan.
- **"Ghost no responde"** → casi siempre es el token (paso de arriba) o que el gateway
  de OpenClaw no está corriendo en ese equipo.
