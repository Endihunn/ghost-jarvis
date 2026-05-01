# Manual de Calibracion de Voz — Ghost Jarvis

> Ultima actualizacion: 2026-04-27

---

## Por que calibrar

Ghost Jarvis usa tres capas para decidir si hay voz y si debes hablar tu:

```
Microfono → [Ganancia] → [VAD WebRTC] → [Energy fallback] → [STT Whisper] → [Wake word fuzz]
```

Si cualquiera de estas capas esta mal ajustada:
- **Ganancia muy baja** — Whisper no entiende, transcribe palabras inventadas
- **Ganancia muy alta** — ruido de fondo satura el VAD, queue llena, se pierden utterances
- **VAD muy permisivo** — TV, ventilador o musica activan el STT constantemente
- **Umbral de wake muy alto** — no te detecta aunque digas la frase correcta
- **Umbral de wake muy bajo** — palabras comunes activan ghost por error

---

## Como abrir el asistente de calibracion

**Clic derecho en el icono de la bandeja** → **Calibrar deteccion de voz**

Se abre un wizard de 4 pasos. Puedes repetirlo cuando cambies de microfono,
de habitacion o notes que la deteccion empeoro.

---

## Paso 1 — Silencio de fondo

**Objetivo:** medir el piso de ruido ambiente.

**Que hacer:**
1. Calla completamente durante 5 segundos.
2. Apaga musica o TV si puedes.
3. El wizard mide el percentil 95 del volumen en ese periodo.

**Que mide:**
- `Piso de ruido (P95)` — nivel maximo de ruido cuando no hay voz.
- Umbral de energia recomendado = `piso * 3` (minimo 0.20).

**Resultados tipicos:**

| Piso P95 | Calidad | Umbral rec. |
|----------|---------|-------------|
| < 0.04   | Buena   | ~0.20       |
| 0.04–0.12 | Moderada | 0.20–0.36  |
| > 0.12   | Alta — considera auriculares o reducir ruido | 0.36–0.55 |

**Que NO hacer:** no hablar, no teclear, no mover el raton cerca del micro.

---

## Paso 2 — Nivel de voz

**Objetivo:** ajustar `mic_gain` para que tu voz llegue al nivel optimo.

**Que hacer:**
1. Habla en tono normal durante 5 segundos.
2. Dile algo a Ghost como si fuera una instruccion real.
3. No grites ni susurres — voz de conversacion normal.

**Que mide:**
- `Nivel P80` — tu voz tipica al 80° percentil.
- `Pico` — el maximo de tu voz.
- `SNR` — relacion senal/ruido (voz vs piso de ruido).

**Formula de ganancia recomendada:**
```
rec_gain = gain_actual * (0.45 / nivel_P80)
```
El objetivo es que P80 de tu voz quede cerca de 0.45 (zona azul del VU meter).

**Resultados tipicos:**

| SNR | Calidad | Accion |
|-----|---------|--------|
| > 10x | Excelente | Mantener configuracion |
| 5–10x | Buena | Ajustes menores de ganancia |
| 2–5x | Marginal | Subir ganancia o acercarse al micro |
| < 2x  | Pobre | Reducir ruido ambiental o usar auriculares con micro |

**Senal de VAD automatico:**
- El wizard ajusta `vad_aggressiveness` segun el SNR:
  - SNR > 10x → agresividad 3 (mas estricto, menos falsos positivos)
  - SNR 5–10x → 2 (valor por defecto)
  - SNR 2–5x → 1 (mas permisivo)
  - SNR < 2x → 0 (maximo permisivo)

---

## Paso 3 — Prueba de wake word

**Objetivo:** verificar que la deteccion de la frase de activacion funciona.

**Que hacer:**
1. Di en voz alta una de tus frases de activacion (ej. "oye ghost", "ghost").
2. Observa el resultado en tiempo real.
3. Repite varias veces si el score varia.

**Que muestra:**
- **Texto STT** — exactamente lo que Whisper transcribio.
- **Puntaje de coincidencia** — valor 0–100 del algoritmo fuzzy (RapidFuzz).
- **Estado** — DETECTADA (verde) o NO detectada (rojo).

**Interpretacion del puntaje:**

| Puntaje | Interpretacion |
|---------|---------------|
| 90–100  | Coincidencia perfecta o casi perfecta |
| 78–89   | Dentro del umbral (detecta por defecto) |
| 60–77   | Cerca del umbral — considera bajar el umbral a `puntaje - 3` |
| < 60    | Muy bajo — Whisper transcribio mal o frase muy corta |

**Ajuste del umbral (`wake_fuzz_threshold`):**
- **Valor por defecto: 78**
- Si el puntaje tipico es 70–75 y no te detecta: baja a 70.
- Si hay falsos positivos (Ghost despierta sin que lo llames): sube a 82–85.
- Nunca bajar de 60 (demasiados falsos positivos).

**Causa comun de puntaje bajo:**
- Pronunciar "ghost" como "gost" o "ghos" — prueba agregar esa variante a las wake phrases.
- Ruido ambiental que confunde a Whisper — mejora el SNR primero (Paso 2).
- Nombre en otro idioma — Whisper small maneja ES y EN; el idioma se auto-detecta.

---

## Paso 4 — Aplicar y guardar

**Que hace:**
- Muestra los cambios recomendados vs valores actuales.
- Permite ajustar manualmente antes de guardar.
- Hace `apply` en caliente (sin reiniciar) de `mic_gain` y `vad_aggressiveness`.
- Guarda en `config.json`.

**Parametros que puedes editar aqui:**

| Parametro | Rango | Descripcion |
|-----------|-------|-------------|
| Ganancia de microfono | 0.5–8.0 | Amplificacion de la senal antes del VAD |
| Agresividad VAD | 0–3 | 0=mas sensible, 3=mas estricto con ruido |
| Timeout de silencio | 200–2000 ms | Cuanto silencio define fin de utterance |
| Umbral de wake word | 50–100 | Puntaje minimo para activar Ghost |

**Nota:** los cambios de `vad_aggressiveness` y `mic_gain` se aplican al instante.
Los demas requieren reiniciar la app para tomar efecto.

---

## Guia rapida por sintoma

### "Ghost no me escucha nunca"
1. Abre el Log de captura de voz (bandeja → Log de captura de voz).
2. Di algo y observa si aparece algun evento.
   - Si no aparece nada: el microfono no esta capturando. Verifica dispositivo de entrada en Windows.
   - Si aparece `STT queue full`: el ruido ambiente llena la cola. Sube el umbral de energia.
   - Si aparece `STT filtered (lang=xx)`: Whisper no identifica ES/EN. Comprueba que no haya TV en idioma extrano.
3. Ejecuta el wizard de calibracion.

### "Ghost me entiende mal las palabras"
- Modelos STT: `tiny` < `small` < `medium`. Actualmente en `small` (recomendado).
- Para nombres en ingles: el auto-detect de idioma mejora mucho vs forzar espanol.
- Si hay mucho eco (altavoces sin auriculares): considera usar audifonos.

### "Ghost despierta solo sin que lo llame"
1. Sube `wake_fuzz_threshold` a 82–85.
2. Sube `vad_aggressiveness` a 3.
3. Revisa que `"ghost"` como wake phrase corta no este en la lista si hay voces en TV/musica que digan esa palabra.

### "Ghost responde cuando hay musica de fondo"
- El filtro de idioma (`es`/`en` con confianza >= 55%) deberia filtrar musica en otros idiomas.
- Si la musica es en espanol o ingles, es mas dificil de filtrar por idioma.
- Opciones: subir VAD agresividad a 3, subir umbral de energia, o usar auriculares.

### "La respuesta de Ghost tiene eco de su propia voz"
- Ghost pausa el microfono durante SPEAKING (`pause_input()`), por lo que no deberia capturarse a si mismo.
- Si ocurre, revisa que `jarvis_voice_effects` este activo (genera audio menos parecido a una voz humana tipica).

---

## Parametros avanzados (config.json)

Estos no estan en el wizard pero se pueden editar directamente en `config.json`:

| Parametro | Default | Descripcion |
|-----------|---------|-------------|
| `mic_auto_gain` | true | Ajuste automatico de ganancia basado en historial |
| `mic_target_level` | 0.15 | Nivel objetivo para auto-gain |
| `ring_buffer_ms` | 400 | Audio previo incluido al inicio de utterance |
| `min_utterance_ms` | 300 | Duracion minima valida de utterance |

---

## Configuracion optima por entorno

### Escritorio silencioso (casa, sin TV)
```json
"mic_gain": 2.0,
"vad_aggressiveness": 2,
"silence_timeout_ms": 450,
"wake_fuzz_threshold": 78
```

### Oficina con ruido de fondo moderado
```json
"mic_gain": 3.0,
"vad_aggressiveness": 3,
"silence_timeout_ms": 500,
"wake_fuzz_threshold": 80
```

### Con TV o musica encendida
```json
"mic_gain": 3.5,
"vad_aggressiveness": 3,
"silence_timeout_ms": 550,
"wake_fuzz_threshold": 82
```

### Headset/auriculares con microfono incorporado
```json
"mic_gain": 1.5,
"mic_auto_gain": false,
"vad_aggressiveness": 2,
"wake_fuzz_threshold": 78
```

---

*Para mas detalles tecnicos ver `OPERATIVIDAD.md`.*
