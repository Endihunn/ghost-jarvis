# Ghost Jarvis — Optimizaciones y Mejoras

Documento técnico de las mejoras aplicadas durante la auditoría y optimización.

---

## Resumen de Cambios

| Área | Cambios Principales | Impacto |
|------|---------------------|---------|
| **GPU / STT** | Whisper small en CUDA float16 | ~5-10× reducción de latencia STT vs CPU |
| **VAD / Audio** | Auto-gain, buffers configurables, umbral adaptable | Mejor captura de voz en ambientes ruidosos |
| **Wake Words** | Nuevas frases tipo "jarvis", fuzzy matching mejorado | Más natural activar al asistente |
| **Voz** | Pipeline de efectos J.A.R.V.I.S. con pedalboard | Voz robótica/metálica reconocible |
| **Visuales** | Shaders avanzados, partículas, grid, scanlines, glitch | Interfaz holográfica tipo Iron Man |
| **Configuración** | 4 nuevas pestañas en diálogo de config | Control total del usuario |

---

## 1. GPU Whisper

### Antes
```python
WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=4)
```

### Después
```python
from gpu_utils import get_optimal_whisper_config
cfg = get_optimal_whisper_config(model_size="small")  # auto-detecta CUDA
WhisperModel("small", device=cfg["device"], compute_type=cfg["compute_type"])
```

- **Dispositivo:** NVIDIA RTX 4070 Laptop (CUDA 12.6)
- **Modelo en uso:** `small` (mejor precisión que `tiny`, latencia aún baja en GPU)
- **Latencia estimada CPU (small):** ~600-1200 ms
- **Latencia estimada GPU (small, float16):** ~80-150 ms
- **VRAM usada:** ~600 MB

### Archivos nuevos
- `gpu_utils.py` — Detección de CUDA, configuración óptima, monitoreo VRAM

---

## 2. Motor de Audio Mejorado

### Auto-Gain
- Histórico de 50 muestras de volumen
- Adaptación suave (2% por evaluación)
- Rango de ganancia: 0.5× a 8.0×

### VAD Configurable
- Agresividad VAD ajustable (0-3)
- Timeout de silencio configurable (200-2000 ms)
- Buffer previo configurable (200-1500 ms)
- Mínimo utterance configurable (100-1000 ms)

### Wake Words
- Frases tipo J.A.R.V.I.S. añadidas: `"jarvis"`, `"oye jarvis"`, `"a la orden jarvis"`
- Umbral de fuzzy matching configurable

### Espectro de Audio
- FFT de 8 bandas enviadas al visualizador
- Reactividad real en estado LISTENING

---

## 3. Efectos de Voz J.A.R.V.I.S.

### Pipeline (pedalboard)
1. **Compresor** — threshold -18 dB, ratio 4:1 (tipo radio/comm)
2. **Filtro LPF** — corte a 3800 Hz (sheen metálico)
3. **Chorus** — rate 0.5 Hz, depth 0.15 (doble voz robótica)
4. **Delay** — 80 ms, feedback 15% (eco de computadora)
5. **Reverb** — room size 0.35, damping 0.6 (cámara metálica)
6. **Gain** — +1.5 dB boost final

### Pitch Shift
- Desplazamiento de -2 semitonos por defecto
- Implementado con torchaudio.functional.pitch_shift
- Tono más grave y autoritario

### Wake Responses Estilo Jarvis
- `"¿Sí, señor?"`
- `"A sus órdenes"`
- `"Escuchando"`
- `"Sistemas en línea"`
- `"En espera de instrucciones"`
- `"Confirmado"`

### Archivos nuevos
- `voice_effects.py` — Pipeline de efectos, cache de audio procesado

---

## 4. Visuales High-Tech

### Paleta de Colores
| Estado | Color | Hex |
|--------|-------|-----|
| IDLE | Cyan neón | `#00d4ff` |
| WAKE | Blanco flash | `#ffffff` |
| LISTENING | Verde menta | `#00ff88` |
| PROCESSING | Ámbar | `#ff6b00` |
| SPEAKING | Cyan neón | `#00d4ff` |
| STANDBY | Rojo alerta | `#ff3333` |

### Shaders Nuevos
- **Fresnel** — Brillo en bordes de cubos
- **Scanlines** — Líneas horizontales tipo CRT/holograma (procesando)
- **Glitch** — Aberración cromática sutil (procesando)
- **Vertex displacement** — Deformación por audio y estado

### Efectos Visuales
- **64 partículas orbitales** — Puntos de luz orbitando el centro
- **Grid holográfico** — Suelo perspectivo con pulso
- **Anillos de expansión** — Ondas que se expanden en transiciones de estado
- **Reactivadad al espectro** — Cubos responden a 8 bandas de frecuencia

### Optimizaciones de Render
- Matrices de proyección/view precalculadas en resizeGL
- Reducción de draw calls
- VAOs separados para cubos, partículas, grid y anillos

---

## 5. Configuración Extendida

### Nuevas pestañas en ConfigDialog
1. **Audio Avanzado** — VAD, auto-gain, timeouts
2. **GPU** — Toggle CUDA, info de hardware
3. **Voz y Visuales** — Efectos J.A.R.V.I.S., calidad visual, toggles

### Nuevos campos en config.json
```json
{
  "vad_aggressiveness": 2,
  "silence_timeout_ms": 450,
  "min_utterance_ms": 250,
  "ring_buffer_ms": 600,
  "mic_auto_gain": true,
  "mic_target_level": 0.25,
  "gpu_enabled": true,
  "gpu_compute_type": "float16",
  "jarvis_voice_effects": true,
  "jarvis_reverb": 0.15,
  "jarvis_delay": 0.12,
  "jarvis_pitch_shift": -2,
  "jarvis_compressor": true,
  "jarvis_chorus": 0.2,
  "visual_quality": "high",
  "particles_enabled": true,
  "scanlines_enabled": true,
  "grid_enabled": true,
  "wireframe_enabled": true,
  "glitch_enabled": true
}
```

---

## 6. Tests

### test_gpu.py
Benchmark CPU vs GPU para Whisper. Mide latencia de inferencia.

### test_audio.py
Valida pipeline de efectos de voz, pitch shift y procesado completo.

### test_visual.py
Verifica que todos los shaders compilan y las transiciones de estado funcionan.

---

## Dependencias Nuevas

```
torch>=2.11.0+cu126
torchaudio>=2.11.0+cu126
pedalboard>=0.9.0
pydub>=0.25.0
```

---

## Métricas

| Métrica | Antes | Después |
|---------|-------|---------|
| Latencia STT | ~600-1200 ms (CPU, small) | ~80-150 ms (GPU, small float16) |
| Wake word phrases | 15 | 18 (incl. jarvis) |
| Efectos de voz | 0 | 6 (compresión, filtro, chorus, delay, reverb, gain) |
| Shaders | 1 (simple) | 4 (cubos, partículas, grid, anillos) |
| Partículas | 0 | 64 orbitales |

---

## Notas

- La app requiere reinicio para cambiar configuración de GPU (Whisper se carga una sola vez).
- Los efectos de voz se procesan offline (post-TTS) para evitar latencia en reproducción.
- El cache de audio procesado se guarda en `assets/jarvis_cache/`.
