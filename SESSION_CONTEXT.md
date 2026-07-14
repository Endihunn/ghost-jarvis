# Contexto de Sesión — Ghost Jarvis

> Archivo temporal para recordar estado entre sesiones. No es un sistema de memoria persistente.

## Sesión 2026-05-18 — Regresión WAKE → LISTENING

### Síntoma
Al decir "hey ghost", el agente contesta la wake response ("Listo, te escucho")
pero **nunca entra a LISTENING**. El log de `logs/ghost-jarvis.log` muestra:

```
07:08:49 [STATE] IDLE -> WAKE
07:08:49 Ducked 2 foreign session(s) to 8%
07:08:49 [STATE] WAKE -> IDLE       ← regresa a IDLE en el mismo segundo
07:08:49 Restored volume for 2 session(s)
```

Nunca aparece `[STATE] WAKE -> LISTENING`.

### Diagnóstico
- Último commit estable: `43744e4` (2026-05-01).
- Hay ~1,200 líneas modificadas **sin commitear** desde entonces:
  - `main.py` +291 (refactor WAKE: `_proceed_after_wake_tts`, `_on_wake_speech_done`,
    flags `_ghost_in_flight` / `_concurrent_response` / `_concurrent_error`, "soplo único").
  - `audio_engine.py` +213 (ducking, listen_mode, etc.).
  - `ghost_bridge.py` +298 (Gemini → OpenClaw — pero esta migración ya estaba
    parcialmente en el HEAD: `ghost_bridge.py` committeado ya habla con
    `~/.openclaw/identity`).
  - `system_volume.py` +124 (nuevo: ducking).
- El committeado (1-mayo) usa polleo simple `QTimer.singleShot(60, _wait_wake_done)`
  con tope de 5 s y `transition(LISTENING)` incondicional ([main.py:184-197](main.py)).
- El sin commitear depende de la señal `speech_finished` y de branching en
  `_proceed_after_wake_tts`. Esa señal se emite desde un `threading.Thread`
  (no `QThread`) → con `AutoConnection` los slots corren en el hilo del emisor,
  abre carrera con `_tts_busy` y `stop_speaking()`.

### Acción tomada
```
git stash push -u -m "wake-listening-regression-debug-2026-05-18"
```
- Working tree limpio en `43744e4`.
- Stash recuperable con `git stash pop` (`stash@{0}`).
- Archivos huérfanos guardados en el stash: `gemini_bridge.py`,
  `gemini_bridge.py.bak`, `config.json.backup.pre-openclaw-bridge-2026-05-13`,
  el `SESSION_CONTEXT.md` previo.

### Pendiente al retomar
1. **Reiniciar Ghost Jarvis** (PID 25480 al cerrar la sesión tenía el código viejo
   ya cargado en memoria — stash no afecta proceso en marcha).
2. Probar "hey ghost ¿qué hora es?" con el código del 1-mayo.
3. Según resultado:
   - **Funciona** → `git stash pop` y rescatar por partes:
     - primero `system_volume.py` (ducking, aislado);
     - luego cambios menores de `audio_engine.py`;
     - dejar el refactor WAKE de `main.py` al final (o tirarlo).
   - **No funciona** → no es el refactor. Habilitar
     `logging.getLogger("state").setLevel(logging.DEBUG)` para que
     `state_machine.py:64-66` imprima el stack del culpable de `transition(IDLE)`.
4. Verificar gateway OpenClaw vivo en `ws://127.0.0.1:18789` (config en
   `config.json`).

### Sospechosos principales del refactor (si el stash confirma regresión)
- `speech_finished` emitida desde thread no-Qt → DirectConnection → race con
  `_tts_busy` que `stop_speaking()` justo puso en False antes de iniciar el
  nuevo TTS.
- `_proceed_after_wake_tts` corriendo en el hilo de playback, no en main thread.

## Estado previo (sesiones anteriores)

### Repo GitHub
- **URL**: https://github.com/Endihunn/ghost-jarvis
- **Pública**: sí
- **Rama principal**: `master`

### Releases
- `v1.0.0` — primer release (builds fallaron)
- `v1.0.1` — release con arreglos cross-platform + MSI (CI/CD en progreso)

### Notas de seguridad
- No hay tokens de GitHub guardados en este sistema actualmente
- `config.json` local contiene `session_key` encriptada con DPAPI (solo funciona
  en esta máquina)
