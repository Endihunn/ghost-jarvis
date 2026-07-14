# Operatividad de Ghost Jarvis

Documento técnico de funcionamiento. Cubre el ciclo de vida, la máquina de estados, el flujo de audio/voz y la comunicación con el agente Endiku (vía OpenClaw).

---

## 1. Ciclo de vida general

```
[Arranque] → [Pre-carga Whisper] → [IDLE] → ...loop... → [Cierre por bandeja]
```

1. **`main.py`** crea la ventana PyQt6 (overlay transparente, sin bordes, siempre encima).
2. Se inicializa el widget OpenGL (`visual_gl.py`) — grid de 9 cubos en rombo.
3. Se carga la máquina de estados (`state_machine.py`).
4. Se inicializa el motor de audio (`audio_engine.py`) — micrófono, VAD, pygame, pyttsx3/edge-tts.
5. Se precarga **Whisper tiny** en segundo plano (~200 ms después del arranque).
6. Se arranca el icono de bandeja del sistema (tray).
7. La app entra en estado **IDLE** y comienza a escuchar.

**Cierre:** solo vía menú contextual de la bandeja → "Salir". Cerrar la ventana la minimiza a la bandeja.

---

## 2. Máquina de estados (FSM)

```
                    +---------+
    +-------------->|  IDLE   |<------------------+
    |               +---------+                   |
    |                    |                        |
    |           (wake word detectada)              |
    |                    v                        |
    |               +---------+                   |
    |               |  WAKE   | --900ms-->        |
    |               +---------+                   |
    |                    |                        |
    |                    v                        |
    |            +------------+                   |
    |            | LISTENING  | --10s sin voz-->  |
    |            +------------+                   |
    |                    |                        |
    |           (utterance completa)               |
    |                    v                        |
    |           +------------+                    |
    +-----------| PROCESSING |<--+                |
    |           +------------+   |                |
    |                    |       | (error)        |
    |           (respuesta lista)|                |
    |                    v       |                |
    |            +-----------+   |                |
    +------------|  SPEAKING |---+ (si pregunta)  |
                 +-----------+                    |
```

| Estado | Qué hace | Visual | Audio |
|--------|----------|--------|-------|
| **IDLE** | Escucha pasiva, busca wake word | Cubos con respiración suave (glow bajo) | Silencio |
| **WAKE** | Confirma despertar, prepara escucha | Flash de glow, escala aumentada | Tono "listen_on", dice respuesta de wake |
| **LISTENING** | Graba utterance del usuario | Pulso sutil, sin reacción al micrófono* | Tono "ready", escucha activa VAD |
| **PROCESSING** | Envía prompt a Endiku, espera | Cubos girando rápido, wave radial | Silencio |
| **SPEAKING** | Lee respuesta en voz alta | Cubos reactivos al habla de Jarvis | TTS (Edge-TTS o local) |

\* *Los cubos nunca reaccionan al micrófono del usuario; solo a la voz de Jarvis (fake volume durante SPEAKING).*

**Timeout de LISTENING:** 10 segundos sin detectar voz → vuelve a IDLE.
**Timeout global de actividad:** 60 segundos en cualquier estado distinto a IDLE → fuerza regreso a IDLE.

---

## 3. Flujo de audio (microfono → texto)

```
Micrófono (16kHz, mono, 16bit)
    │
    ▼
[Ring buffer] ← 400ms de audio previo
    │
    ▼
[VAD] (WebRTC VAD, agresividad 2)
    │
    ▼
[Detección de voz] → triggered = true
    │
    ▼
[Buffer de utterance] ← se acumula audio mientras haya voz
    │
    ▼
[Silencio ≥350ms] → utterance completa
    │
    ▼
[Whisper tiny] (memoria RAM, sin disco)
    │
    ▼
[Transcripción en español] → texto lower-case
    │
    ▼
[¿Contiene wake phrase?] ──Sí──► on_wake() + on_utterance(texto limpio)
         │
         No
         ▼
    on_utterance(texto completo)
```

**Parámetros clave:**
- `SAMPLE_RATE = 16000`
- `CHUNK_DURATION_MS = 30` (480 muestras por chunk)
- `mic_gain = 3.0` (amplificación de señal antes del VAD)
- `silence_limit = 0.35s` (fin de utterance)
- `min_utterance_bytes = 0.35s` (utterance mínima válida)

**Whisper config (ultra-rápido):**
- `beam_size=1`, `best_of=1`
- `condition_on_previous_text=False`
- `without_timestamps=True`
- `vad_filter=False` (VAD externo via webrtcvad; Whisper VAD desactivado)

---

## 4. Wake word y frases de activación

Configurables en `config.json` (`wake_phrases`). Actualmente:

- `"oye endiku"`
- `"oiga endiku"`
- `"oye endik"`
- `"oiga endik"`
- `"ey endiku"`
- `"ei endiku"`
- `"endiku"`

Al detectar una wake phrase:
1. Se elimina la frase del texto transcrito.
2. Se llama `on_wake()` (transición a estado WAKE).
3. Si queda texto limpio, se envía como utterance inmediata (maneja casos donde usuario dice "oye endiku dime la hora" en una sola breath).

**Respuestas de voz al despertar** (aleatorias):
> *"Dime"*, *"Aquí estoy"*, *"Te escucho"*, *"Adelante"*, *"Sí"*, *"Aquí presente"*, *"A la orden"*, *"Atento"*, *"¿Qué necesitas?"*, *"Escuchando"*

---

## 5. Comunicación con Endiku (Ghost Bridge)

```
[PROCESSING]
    │
    ▼
[GhostBridge.send(prompt)]
    │
    ▼
[GhostWorker QThread] → GatewayWS.send_message(prompt)
    │
    ▼
[WebSocket ws://127.0.0.1:18789] ── Protocol 3 ──► [OpenClaw Gateway]
    │                                                        │
    │                                              Auth: Bearer token
    │                                              + ED25519 device sig
    │
    ▼
[chat.send req] ──► [Agente main (streaming)]
    │
    ▼
[chat events: delta/final] ←─ acumulación de texto
    │
    ▼
[Heurística is_question()] → ¿session_active?
    │
    ▼
[SPEAKING] → TTS → o [LISTENING] si era pregunta
```

**Protocolo WebSocket (Protocol 3):**
1. HTTP Upgrade con `Authorization: Bearer <gateway_token>`
2. Servidor envía `connect.challenge` con nonce
3. Cliente firma con ED25519 (`~/.openclaw/identity/device.json`) y envía `connect`
4. Servidor responde `hello-ok`; se inicia recv loop en hilo background
5. Cada prompt envía `chat.send`; la respuesta llega como eventos `chat` streaming

**Manejo de errores:**
- Gateway no disponible → modo STANDBY, retry cada 30s (health check HTTP `/health`)
- Timeout (>240s) → *"Ghost tardó demasiado en responder"*
- Agent error/abort → error hablado, vuelve a IDLE
- Reconexión automática: si el WS se corta, el siguiente `send_message` reconecta

**Sesión persistente:**
`session_key` fijo por instancia (`agent:main:<8hex>`). El agente mantiene contexto de conversación mientras la sesión esté activa. Si Endiku hace una pregunta, `session_active = true` y tras SPEAKING la app vuelve a LISTENING automáticamente.

---

## 6. Sistema de voz (TTS)

### 6.1 TTS local (pyttsx3)
- **Uso:** respuestas de wake (instantáneas, ~100ms).
- **Voz:** voz por defecto del sistema (anteriormente Sabina femenina, ahora no aplica porque edge está activo).
- **Rate:** `tts_local_rate = 185` palabras/minuto.

### 6.2 Edge-TTS (agente)
- **Uso:** respuestas largas de Endiku.
- **Voz actual:** `es-MX-JorgeNeural` (masculina, latinoamericana).
- **Rate:** `+5%` (ligera aceleración para fluidez).
- **Flujo:** genera MP3 temporal → pygame reproduce → borra MP3.
- **Fallback:** si Edge-TTS falla, usa pyttsx3 local.

**Detección de "está hablando":**
```python
is_speaking() = tts_busy OR pygame.mixer.get_busy()
```

---

## 7. Visual (OpenGL)

### Renderizado
- **Motor:** OpenGL 3.3 core via PyQt6 `QOpenGLWidget`.
- **Geometría:** 9 cubos en layout diamante (1-2-3-2-1).
- **Shaders:** vertex + fragment propios. Efectos:
  - **Breath:** sinusoide por tiempo + posición (respiración).
  - **Glow aditivo:** pasada halo (blend `SRC_ALPHA + ONE`) + pasada sólida.
  - **Reacción a voz:** solo durante SPEAKING (fake volume via `sin(time*8)`).
  - **Spin:** rotación rápida durante PROCESSING.

### Transparencia
- Ventana: `FramelessWindowHint + Tool + WA_TranslucentBackground`.
- OpenGL: `glClearColor(0,0,0,0)` + alpha buffer.
- Fallback si shaders fallan: círculo cyan vía `QPainter`.

### Interacción
- **Arrastrable:** click + drag en cualquier parte del overlay.
- **Bandeja:** clic derecho en icono → Mostrar / Iniciar con Windows / Salir.

---

## 8. Configuración operativa clave

Archivo: `config.json`

```json
{
  "openclaw_cmd": "C:/Users/darth/AppData/Roaming/npm/openclaw.cmd",
  "openclaw_config": "C:/Users/darth/.openclaw/openclaw.json",
  "ghost_prompt_prefix": "[INSTRUCCION: Responde SIEMPRE en español...] ",
  "overlay_width": 420,
  "overlay_height": 420,
  "wake_phrases": ["oye endiku", "oiga endiku", ...],
  "mic_gain": 3.0,
  "tts_use_edge": true,
  "tts_voice": "es-MX-JorgeNeural",
  "tts_rate": "+5%",
  "tts_local_rate": 185,
  "visual_fps": 60
}
```

**No tocar en caliente:** `config.json` se lee al inicio. Cambios requieren reiniciar la app.

---

## 9. Manejo de errores y fallbacks

| Componente | Fallo posible | Fallback / Comportamiento |
|------------|---------------|---------------------------|
| Micrófono | No disponible | App sigue corriendo visualmente. Logs en consola/app.log. |
| Whisper | No carga modelo | Silencio (no transcribe). No crashea. |
| VAD | webrtcvad falla | Fuerza `is_speech = True` si vol > 0.15 (detección por volumen). |
| OpenGL | GPU no soporta 3.3 | Dibuja círculo cyan de fallback. |
| OpenClaw | No instalado | Mensaje hablado: instrucciones de instalación. |
| Gateway | Cerrado/caído | Modo embedded (lento, sin sesión persistente). |
| Edge-TTS | Sin internet | Fallback a pyttsx3 local. |
| pyttsx3 | Sin voces | Excepción logueada, no hay TTS. |

---

## 10. Archivos de log

- **`app.log`** (generado por `launch.bat`): stdout/stderr de la sesión actual.
- Útil para diagnosticar: errores de Whisper, fallos de micrófono, respuestas de OpenClaw, etc.

---

## 11. Diagrama resumido de operación

```
Usuario dice: "oye endiku, ¿qué hora es?"
                    │
                    ▼
┌──────────────────────────────────────────────────────────────┐
│  [AUDIO ENGINE]                                               │
│  • Graba utterance via VAD                                    │
│  • Whisper transcribe: "oye endiku ¿qué hora es?"            │
│  • Detecta wake → on_wake() + on_utterance("¿qué hora es?")  │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────────┐
│  [STATE MACHINE]                                              │
│  IDLE → WAKE → LISTENING → PROCESSING                        │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────────┐
│  [GHOST BRIDGE]                                               │
│  • GatewayWS.send_message("¿qué hora es?")                   │
│  • WebSocket Protocol 3, streaming chat events               │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────────────┐
│  [SPEAKING]                                                   │
│  • Edge-TTS genera voz de Jorge: "Son las 4:40 PM"           │
│  • Visual reacciona al "speech volume" fake                   │
│  • ¿Es pregunta? → vuelve a LISTENING : vuelve a IDLE        │
└──────────────────────────────────────────────────────────────┘
```

---

*Última actualización: 2026-04-26*
