# Ghost Jarvis — Asistente Personal por Voz

Asistente de escritorio tipo **J.A.R.V.I.S.** conectado al agente **main** de OpenClaw local. Escucha continuamente, reacciona al trigger de voz **"oye, ghost"** o **"jarvis"**, responde con TTS estilo J.A.R.V.I.S., envía prompts al agente Ghost y mantiene flujos de conversación cuando Ghost hace preguntas.

## Características

- **Respuesta en streaming (v1.1)**: habla la primera oración ~2 s después de que el agente empieza a contestar — sintetiza la siguiente oración mientras suena la actual, en vez de esperar la respuesta completa
- **Barge-in (v1.1)**: el micrófono sigue abierto mientras Ghost habla o piensa — "ghost, cállate" corta la voz en seco y la wake word cancela un run en curso (con echo-guard para no oírse a sí mismo)
- **Comandos locales (v1.1)**: "repite", "más alto/más bajo", "cancela" se resuelven al instante sin viajar al agente
- **Markdown → habla (v1.1)**: las respuestas se limpian antes del TTS (sin asteriscos, fences, URLs crudas ni emojis)
- **Wake words**: "oye, ghost", "jarvis", "endiku" y más — detección con fuzzy matching
- **STT**: `faster-whisper` small con **GPU CUDA** (float16) para mejor precisión y baja latencia
- **VAD adaptativo**: Auto-gain, timeouts configurables, buffer previo ajustable
- **TTS local**: `pyttsx3` para frases de confirmación instantáneas
- **TTS agente**: `edge-tts` + **efectos J.A.R.V.I.S.** (compresión, reverb, delay, chorus, pitch shift)
- **Visual**: Interfaz holográfica high-tech con OpenGL 3.3:
  - 9 cubos de cristal en rombo: aristas brillantes con antialiasing, caras de vidrio translúcido e iridiscencia sutil
  - **Bloom HDR real** (2-pass gaussiano vía FBO, con fallback automático al halo legacy)
  - Reflejo de los cubos sobre el grid holográfico del piso
  - Transiciones de estado suavizadas (~180 ms de cross-fade de color)
  - 64 partículas orbitales, scanlines y glitch effects
  - Anillos de expansión en transiciones
  - Reactividad al espectro de audio en tiempo real
- **Transparencia real**: fondo completamente transparente, sin bordes de ventana
- **Click-through**: los clics atraviesan el overlay hacia lo que tengas detrás (configurable). Para moverlo: menú de bandeja → **«Modo mover»**, arrastra, y vuelve a desactivarlo; la posición se recuerda. También hay **«Centrar overlay»** y presets de **tamaño** (260/320/420) en la bandeja.
- **Posición**: centrado en la pantalla principal o donde lo dejes
- **Sonidos**: tonos generados proceduralmente para escuchar/dejar de escuchar
- **Conexión Ghost**: vía `openclaw agent` (OpenClaw)
- **Autoarranque Windows**: opción desde el menú de bandeja
- **Configuración extendida**: 6 pestañas (Conexión, Wake Words, SOUL, Audio, GPU, Voz/Visuales)

## Requisitos

- Windows 10/11
- Python 3.10+ (probado en 3.13)
- Micrófono
- **GPU NVIDIA con CUDA 12.6+** (opcional pero recomendado para STT acelerado)
- Kimi Desktop instalado (solo si quieres conectar con Ghost)

## Instalación

```powershell
# 1. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate

# 2. Instalar dependencias (incluye torch CUDA 12.6)
pip install -r requirements.txt

# 3. (Opcional) Agregar al inicio de Windows
python startup_installer.py
```

> **Nota sobre torch:** Si `requirements.txt` falla con torch, instálalo manualmente:
> ```powershell
> pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
> pip install -r requirements.txt
> ```

## Configuración

Edita `config.json` (se crea automáticamente al primer arranque) o usa el diálogo de configuración desde la bandeja:

```json
{
  "openclaw_cmd": "C:/Users/darth/AppData/Roaming/npm/openclaw.cmd",
  "openclaw_config": "C:/Users/darth/.openclaw/openclaw.json",
  "tts_voice": "es-MX-JorgeNeural",
  "tts_rate": "+5%",
  "gpu_enabled": true,
  "jarvis_voice_effects": true,
  "jarvis_reverb": 0.15,
  "jarvis_delay": 0.12,
  "jarvis_pitch_shift": -2,
  "visual_quality": "high",
  "particles_enabled": true,
  "scanlines_enabled": true,
  "grid_enabled": true
}
```

- `openclaw_config` apunta a tu instancia OpenClaw.
- `gpu_enabled`: activa CUDA para Whisper (requiere reinicio).
- `jarvis_voice_effects`: aplica pipeline de efectos a la voz del agente.

## Ejecución

```powershell
.venv\Scripts\pythonw.exe main.py
```

O haz doble clic en el acceso directo de escritorio.

## Uso

1. La app aparece como un overlay flotante **sin ventana ni fondo** (320×320 px por defecto) **centrado** en la pantalla, siempre visible encima de todo. Los clics lo **atraviesan**; para arrastrarlo activa **«Modo mover»** en el menú de la bandeja (y desactívalo al terminar).
2. Di **"oye, ghost"** o **"jarvis"** — los cubos reaccionarán con anillos de expansión, sonará un tono y Jarvis dirá `"¿Sí, señor?"`.
3. Habla tu prompt.
4. Jarvis enviará el mensaje a Ghost y leerá la respuesta con voz robótica estilo J.A.R.V.I.S.
5. Si Ghost hace una pregunta, Jarvis quedará escuchando automáticamente.
6. Haz clic derecho en el icono de la bandeja para mostrar/ocultar, configurar o salir.

## Estructura

```
ghost-jarvis/
├── main.py              # App PyQt6, máquina de estados, ciclo de vida
├── visual_gl.py         # Renderizado OpenGL holográfico high-tech
├── audio_engine.py      # Grabación, VAD, STT GPU, TTS con efectos
├── ghost_bridge.py      # Comunicación con Ghost vía openclaw agent
├── state_machine.py     # FSM: IDLE → WAKE → LISTENING → PROCESSING → SPEAKING
├── config.py            # Configuración local (config.json)
├── config_dialog.py     # Diálogo de configuración (6 pestañas)
├── gpu_utils.py         # Detección CUDA y configuración óptima
├── voice_effects.py     # Pipeline de efectos J.A.R.V.I.S.
├── startup_installer.py # Autoarranque Windows
├── test_gpu.py          # Benchmark CPU vs GPU
├── test_audio.py        # Test pipeline de audio
├── test_visual.py       # Test compilación de shaders
├── launch.bat           # Lanzador batch
├── launch.vbs           # Lanzador silencioso (sin consola)
├── requirements.txt     # Dependencias
├── assets/
│   ├── sounds/          # WAV generados
│   ├── tts_cache/       # Cache TTS edge-tts
│   └── jarvis_cache/    # Cache TTS procesado con efectos
├── models/              # faster-whisper small (descarga automática, ~466 MB)
└── config.json          # Configuración de usuario
```

## Tests

```powershell
# Benchmark STT CPU vs GPU
python test_gpu.py

# Validar pipeline de efectos de voz
python test_audio.py

# Validar compilación de shaders OpenGL
python test_visual.py
```

## Notas de seguridad

- Ghost Jarvis **nunca lee ni escribe** dentro de `.kimi_openclaw/`, `.kimi/` ni directorios similares.
- La sesión y el directorio de trabajo de Ghost son configurables por el usuario y **opcionales**.
- El modelo `small` de Whisper se descarga automáticamente en `models/` en la primera ejecución (~466 MB).

## Solución de problemas

- **No se ven los cubos**: asegúrate de que tu GPU soporta OpenGL 3.3+. Si el shader falla, verás un círculo cyan de fallback.
- **El micrófono no funciona**: comprueba los permisos de micrófono en Windows. La app sigue funcionando visualmente aunque el audio falle.
- **Ghost no responde**: verifica que `openclaw` esté instalado (`npm install -g openclaw`) y que `openclaw_config` apunte a la instancia correcta.
- **Torch no instala**: asegúrate de usar Python 3.10-3.13 en Windows. Si falla, instala manualmente con `pip install torch --index-url https://download.pytorch.org/whl/cu126`.

## Créditos

- OpenClaw / Kimi Desktop — Backend de agentes
- faster-whisper — STT offline por Systran
- edge-tts — TTS neural de Microsoft
- pedalboard — Efectos de audio profesionales por Spotify
