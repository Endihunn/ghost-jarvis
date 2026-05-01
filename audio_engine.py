"""Audio engine: recording, VAD, STT, TTS, sound effects.

Optimizado para latencia mínima:
- STT directo desde memoria (sin escribir a disco).
- GPU Whisper cuando CUDA está disponible.
- VAD adaptativo con auto-gain.
- TTS local con pyttsx3 por defecto (instantáneo).
- TTS agente con efectos J.A.R.V.I.S. post-procesados.
"""
import asyncio
import hashlib
import io
import os
import queue
import re
import sys
import wave
import threading
import time as time_mod
import math
import logging
import numpy as np
from pathlib import Path
from typing import Callable, Optional

import pyaudio
import webrtcvad
import pygame.mixer
import pyttsx3
from rapidfuzz import fuzz
from PyQt6.QtCore import QObject, pyqtSignal

from config import APP_CONFIG
from gpu_utils import get_optimal_whisper_config
from voice_effects import process_audio_jarvis, jarvis_effect_hash

logger = logging.getLogger("audio")

_fwhisper = None
_whisper_lock = threading.Lock()

SAMPLE_RATE = 16000
CHUNK_DURATION_MS = 30
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)
PA_FORMAT = pyaudio.paInt16

WAKE_PHRASES = [p.lower().strip() for p in APP_CONFIG.wake_phrases]
WAKE_FUZZ_THRESHOLD = APP_CONFIG.wake_fuzz_threshold

# Filters for STT post-processing — module-level so the regex compiles once
_JUNK_RE = re.compile(r"^(m+h*|e+h*|a+h*|u+h*|o+h*|s+h+)$")
_CLEAN_RE = re.compile(
    r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ]",
    flags=re.UNICODE,
)

# Reject transcriptions that contain non-latin scripts (cyrillic, cjk, arabic, etc.)
_LATIN_SCRIPT_RE = re.compile(r"^[a-zA-Z0-9\sáéíóúüñÁÉÍÓÚÜÑ]*$")


# ---------------------------------------------------------------------------
# Edge-TTS — persistent asyncio loop avoids spawning python -m edge_tts per call
# ---------------------------------------------------------------------------

class _EdgeTTSRunner:
    """Generates edge-tts MP3 files via a single background asyncio loop.

    Replacing `subprocess.run([sys.executable, '-m', 'edge_tts', ...])` saves
    the ~300-500 ms Python startup overhead on every TTS request.
    """

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                t = threading.Thread(
                    target=loop.run_forever, daemon=True, name="edge-tts-loop"
                )
                t.start()
                self._loop = loop
                self._thread = t
            return self._loop

    def shutdown(self) -> None:
        """Stop the background asyncio loop and wait for the thread to finish."""
        with self._lock:
            if self._loop is None and self._thread is None:
                return
            if self._loop is not None:
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                except Exception:
                    pass
                self._loop = None
            thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def synthesize(self, text: str, voice: str, rate: str, output_path: Path,
                   timeout: float = 20.0) -> None:
        loop = self._ensure_loop()

        async def _run():
            import edge_tts
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(output_path))

        future = asyncio.run_coroutine_threadsafe(_run(), loop)
        future.result(timeout=timeout)


_EDGE_TTS = _EdgeTTSRunner()


def _clean_stt_text(text: str) -> str:
    """Strip punctuation and control chars that Whisper sometimes emits, normalize whitespace."""
    return " ".join(_CLEAN_RE.sub(" ", text).split())


def _is_hallucination_loop(text: str) -> bool:
    """Detect Whisper hallucination: any n-gram repeated ≥3 times covering ≥60% of the text.

    Unlike the original, scans ALL positions so it catches mid-text loops like
    'vamos a ver si... vamos a ver si...' not just leading repetitions.
    """
    words = text.split()
    n = len(words)
    if n < 8:
        return False
    for plen in range(1, min(9, n // 3 + 1)):
        counts: dict = {}
        for i in range(n - plen + 1):
            p = tuple(words[i : i + plen])
            counts[p] = counts.get(p, 0) + 1
        for phrase, count in counts.items():
            if count >= 3 and (count * plen) / n >= 0.60:
                return True
    return False


def _check_wake(text: str) -> tuple[bool, str]:
    """Detect wake word with fuzzy matching to tolerate STT errors.

    Single-word wake phrases (ghost, jarvis, endiku…) use word-level fuzz.ratio
    against each word in the transcript — prevents character-level false matches
    like 'soy' → 'oye ghost' or 'médico' → 'indico'.

    Multi-word phrases (oye ghost, oye jarvis…) use partial_ratio as before.
    """
    text_lower = _clean_stt_text(text).lower().strip()
    if not text_lower or len(text_lower) < 4:
        return False, text_lower
    text_words = text_lower.split()
    best_score = 0
    best_wp = ""
    best_threshold = WAKE_FUZZ_THRESHOLD
    for wp in WAKE_PHRASES:
        if " " not in wp:
            score = max((fuzz.ratio(wp, w) for w in text_words), default=0)
            threshold = max(WAKE_FUZZ_THRESHOLD, 92)
        else:
            score = fuzz.partial_ratio(wp, text_lower)
            threshold = WAKE_FUZZ_THRESHOLD
        if score > best_score:
            best_score = score
            best_wp = wp
            best_threshold = threshold

    # Always log meta at DEBUG (no transcript). Only when the score is high
    # enough to actually fire the wake we log it at INFO — and even then
    # without echoing the full transcript to the persistent log.
    if best_score >= best_threshold:
        if not APP_CONFIG.privacy_mode:
            logger.info("Wake check: score=%d/%d (%s) FIRED", best_score, best_threshold, best_wp)
    else:
        if not APP_CONFIG.privacy_mode:
            logger.debug("Wake check: score=%d/%d (%s) in %r", best_score, best_threshold, best_wp, text_lower)

    if best_score >= best_threshold:
        pattern = re.escape(best_wp)
        clean = re.sub(rf"\b{pattern}\b", "", text_lower).strip(" ,.;:-")
        return True, clean

    return False, text_lower


def _load_whisper():
    global _fwhisper
    if _fwhisper is None:
        with _whisper_lock:
            if _fwhisper is None:
                from faster_whisper import WhisperModel

                model_dir = Path(__file__).with_name("models")
                model_dir.mkdir(exist_ok=True)

                force_cpu = not APP_CONFIG.gpu_enabled
                model_size = "medium"
                cfg = get_optimal_whisper_config(force_cpu=force_cpu, model_size=model_size)

                logger.info(
                    "Loading Whisper %s on %s (compute=%s, threads=%s)...",
                    model_size,
                    cfg["device"].upper(),
                    cfg["compute_type"],
                    cfg["cpu_threads"],
                )
                _fwhisper = WhisperModel(
                    model_size,
                    device=cfg["device"],
                    compute_type=cfg["compute_type"],
                    download_root=str(model_dir),
                    cpu_threads=cfg["cpu_threads"],
                )
                logger.info("Whisper %s ready on %s.", model_size, cfg["device"].upper())
    return _fwhisper


class AudioEngine(QObject):
    wake_detected = pyqtSignal()
    utterance_detected = pyqtSignal(str, bool, str)  # text, is_long (>5s), lang
    volume_changed = pyqtSignal(float)
    spectrum_changed = pyqtSignal(list)  # New: FFT bins for visualizer
    speech_finished = pyqtSignal()  # Emitted when TTS playback ends

    def __init__(self):
        super().__init__()

        self.pa = pyaudio.PyAudio()
        self.vad = webrtcvad.Vad(APP_CONFIG.vad_aggressiveness)
        self.stream: Optional[pyaudio.Stream] = None
        self._input_gain = APP_CONFIG.mic_gain
        self._auto_gain = APP_CONFIG.mic_auto_gain
        self._target_level = APP_CONFIG.mic_target_level
        self._gain_history: list[float] = []

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stt_thread: Optional[threading.Thread] = None
        self._stt_queue: queue.Queue = queue.Queue(maxsize=8)
        self._accept_input = True
        self._speech_frames = 0

        # TTS engines (lazy-init so pyttsx3 doesn't block the UI thread on startup)
        self.tts_local = None
        self._tts_local_lock = threading.Lock()
        self._tts_busy = False
        self._tts_lock = threading.Lock()

        # Cache of Jarvis effect generation failures (avoid retrying every call)
        self._jarvis_failed: set[str] = set()

        # Sound cache — 24000 Hz matches edge-tts native rate
        try:
            pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
        except pygame.error:
            pass
        self._sounds: dict[str, pygame.mixer.Sound] = {}
        self._ensure_sounds()

        # TTS cache (edge-tts pre-generated files)
        self._tts_cache_dir = Path(__file__).with_name("assets") / "tts_cache"
        self._tts_cache_dir.mkdir(parents=True, exist_ok=True)

        # Jarvis-processed cache
        self._jarvis_cache_dir = Path(__file__).with_name("assets") / "jarvis_cache"
        self._jarvis_cache_dir.mkdir(parents=True, exist_ok=True)

        # Spectrum buffer for visualizer
        self._spectrum = [0.0] * 8
        self._spectrum_skip = 0

    def _ensure_sounds(self):
        snd_dir = Path(__file__).with_name("assets") / "sounds"
        snd_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "listen_on": snd_dir / "listen_on.wav",
            "listen_off": snd_dir / "listen_off.wav",
            "alert": snd_dir / "alert.wav",
            "ready": snd_dir / "ready.wav",
        }
        for name, path in files.items():
            if not path.exists():
                if name == "listen_on":
                    self._generate_tone(path, 1200, 0.12, up=True)
                elif name == "listen_off":
                    self._generate_tone(path, 500, 0.12, up=False)
                elif name == "alert":
                    self._generate_tone(path, 1800, 0.08, up=True)
                elif name == "ready":
                    self._generate_ding(path)
            try:
                self._sounds[name] = pygame.mixer.Sound(str(path))
            except Exception as e:
                logger.warning("Failed to load sound %s: %s", name, e)

    @staticmethod
    def _generate_tone(path: Path, freq: float, duration: float, up: bool):
        rate = 24000
        samples = int(rate * duration)
        t = np.linspace(0, duration, samples, endpoint=False)
        if up:
            freq_ramp = freq * (1 + t * 3)
        else:
            freq_ramp = freq * (1 + (duration - t) * 3)
        sig = np.sin(2 * np.pi * freq_ramp * t) * 0.5
        attack = int(0.02 * rate)
        release = int(0.04 * rate)
        env = np.ones(samples)
        if attack:
            env[:attack] = np.linspace(0, 1, attack)
        if release:
            env[-release:] = np.linspace(1, 0, release)
        sig *= env
        pcm = (sig * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())

    @staticmethod
    def _generate_ding(path: Path):
        rate = 24000
        duration = 0.18
        samples = int(rate * duration)
        t = np.linspace(0, duration, samples, endpoint=False)
        sig = (
            np.sin(2 * np.pi * 1500 * t) * 0.4
            + np.sin(2 * np.pi * 2200 * t) * 0.3
        )
        attack = int(0.005 * rate)
        release = int(0.12 * rate)
        env = np.ones(samples)
        if attack:
            env[:attack] = np.linspace(0, 1, attack)
        if release:
            env[-release:] = np.linspace(1, 0, release)
        sig *= env
        pcm = (sig * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())

    def play_sound(self, name: str):
        snd = self._sounds.get(name)
        if snd:
            try:
                snd.play()
            except Exception as e:
                logger.warning("play_sound error: %s", e)

    def start(self):
        if self._running:
            return
        try:
            self.stream = self.pa.open(
                format=PA_FORMAT,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            logger.error("No se pudo abrir el micrófono: %s", e)
            return
        # PyAudio Stream has no get_sample_rate(); if open() succeeded we're at SAMPLE_RATE.
        self._running = True
        self._accept_input = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        self._stt_thread = threading.Thread(target=self._stt_loop, daemon=True)
        self._stt_thread.start()

    def stop(self):
        self._running = False
        self._accept_input = False
        try:
            self._stt_queue.put(None, block=False)
        except queue.Full:
            pass
        if self._stt_thread and self._stt_thread.is_alive():
            self._stt_thread.join(timeout=3.0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        try:
            _EDGE_TTS.shutdown()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass

    def pause_input(self):
        self._accept_input = False

    def resume_input(self):
        self._accept_input = True

    def _update_auto_gain(self, vol: float):
        """Adjust input gain dynamically to keep volume near target level."""
        if not self._auto_gain:
            return
        self._gain_history.append(vol)
        if len(self._gain_history) > 50:
            self._gain_history.pop(0)
        if len(self._gain_history) < 10:
            return
        median_vol = np.median(self._gain_history)
        if median_vol < 0.01:
            return
        error = self._target_level - median_vol
        # Slow adaptation: 2% per evaluation
        adjustment = 1.0 + error * 0.02
        adjustment = np.clip(adjustment, 0.8, 1.2)
        self._input_gain = np.clip(self._input_gain * adjustment, 0.5, 8.0)

    def _compute_spectrum(self, chunk: np.ndarray):
        """Compute a simple 8-bin frequency spectrum for the visualizer."""
        n = len(chunk)
        if n < 64:
            return
        window = np.hanning(n)
        fft = np.fft.rfft(chunk * window)
        magnitude = np.abs(fft)
        # Bin into 8 frequency bands logarithmically
        bands = [0, 2, 4, 8, 16, 32, 64, 128, min(len(magnitude), 256)]
        spectrum = []
        for i in range(8):
            lo, hi = bands[i], bands[i + 1]
            if hi > len(magnitude):
                hi = len(magnitude)
            if lo >= hi:
                spectrum.append(0.0)
                continue
            val = np.mean(magnitude[lo:hi])
            spectrum.append(min(val / 1000.0, 1.0))
        self._spectrum = spectrum
        self.spectrum_changed.emit(spectrum)

    def _listen_loop(self):
        ring_buffer = bytearray()
        triggered = False
        utterance = bytearray()
        silence_frames = 0

        max_pre_bytes = int(SAMPLE_RATE * (APP_CONFIG.ring_buffer_ms / 1000.0)) * 2
        silence_limit = int((APP_CONFIG.silence_timeout_ms / 1000.0) / (CHUNK_DURATION_MS / 1000))
        min_utterance_bytes = int(SAMPLE_RATE * (APP_CONFIG.min_utterance_ms / 1000.0)) * 2
        max_utterance_bytes = int(SAMPLE_RATE * 12.0) * 2
        consecutive_errors = 0

        while self._running:
            try:
                chunk = self.stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception as e:
                consecutive_errors += 1
                logger.error("Mic read error (%d/5): %s", consecutive_errors, e)
                if consecutive_errors >= 5:
                    logger.critical("Too many mic read errors, pausing 2s.")
                    time_mod.sleep(2.0)
                    consecutive_errors = 0
                else:
                    time_mod.sleep(0.05)
                continue

            try:
                arr = np.frombuffer(chunk, dtype=np.int16)
                raw_vol = np.abs(arr).mean() / 32768.0

                arr = np.clip(
                    arr.astype(np.float32) * self._input_gain, -32768, 32767
                ).astype(np.int16)
                # Pre-emphasis: boost high frequencies (consonants like s,t,h)
                # that get lost at distance and masked by fan noise
                arr = np.append(arr[0], arr[1:] - 0.97 * arr[:-1]).astype(np.int16)
                chunk = arr.tobytes()
                vol = np.abs(arr).mean() / 32768.0
                self.volume_changed.emit(min(vol, 1.0))
                self._update_auto_gain(vol)
                self._spectrum_skip = (self._spectrum_skip + 1) % 3
                if self._spectrum_skip == 0:
                    self._compute_spectrum(arr)

                if not self._accept_input:
                    continue

                try:
                    is_speech = self.vad.is_speech(chunk, SAMPLE_RATE)
                except Exception:
                    is_speech = False

                if not triggered:
                    ring_buffer.extend(chunk)
                    if len(ring_buffer) > max_pre_bytes:
                        ring_buffer = ring_buffer[-max_pre_bytes:]
                    if is_speech:
                        self._speech_frames += 1
                        if self._speech_frames >= 2:  # debounce: 60ms of real speech
                            triggered = True
                            utterance.extend(ring_buffer)
                            utterance.extend(chunk)
                            ring_buffer.clear()
                            silence_frames = 0
                            self._speech_frames = 0
                    else:
                        self._speech_frames = 0
                else:
                    utterance.extend(chunk)
                    if is_speech:
                        # Even if VAD thinks there is speech, very low volume for a
                        # sustained period usually means background noise/fan rather
                        # than actual voice. Treat it as silence so the utterance
                        # doesn't grow indefinitely.
                        if vol < 0.04:
                            silence_frames += 1
                        else:
                            silence_frames = 0
                    else:
                        # Energy gate: if volume drops near ambient level for 2+ frames,
                        # count it as silence even if VAD is confused by noise/fan
                        if vol < 0.06:
                            silence_frames += 1
                        else:
                            silence_frames = 0

                    if len(utterance) > max_utterance_bytes:
                        logger.warning("Utterance exceeded %d bytes, forcing split", max_utterance_bytes)
                        try:
                            self._stt_queue.put(bytes(utterance), block=False)
                        except queue.Full:
                            logger.warning("STT queue full, dropping utterance")
                        utterance.clear()
                        triggered = False
                        silence_frames = 0
                        continue

                    if silence_frames >= silence_limit and len(utterance) >= min_utterance_bytes:
                        try:
                            self._stt_queue.put(bytes(utterance), block=False)
                        except queue.Full:
                            logger.warning("STT queue full, dropping utterance")
                        utterance.clear()
                        triggered = False
                        silence_frames = 0

                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error("Listen loop error (%d/%d): %s", consecutive_errors, 5, e)
                if consecutive_errors >= 5:
                    logger.critical("Too many listen loop errors, pausing 2s.")
                    time_mod.sleep(2.0)
                    consecutive_errors = 0
                continue

    def _stt_loop(self):
        while self._running:
            try:
                pcm = self._stt_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if pcm is None:
                break
            self._process_utterance(pcm)

    def _process_utterance(self, pcm_bytes: bytes):
        # Energy gate: if the utterance is mostly silence, skip Whisper entirely.
        # Whisper on near-silent audio wastes GPU and hallucinates filler text
        # (e.g. "subtítulos by..." or repeating Cyrillic glyphs), which then
        # has to be filtered downstream. The threshold (~0.4% of full scale)
        # is well below typical speech but above ambient noise after AGC.
        try:
            arr = np.frombuffer(pcm_bytes, dtype=np.int16)
            if arr.size:
                rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2))) / 32768.0
                peak = float(np.abs(arr).max()) / 32768.0
                if rms < 0.012 and peak < 0.08:
                    logger.debug("STT skipped (silence): rms=%.4f peak=%.3f", rms, peak)
                    return
        except Exception:
            pass

        try:
            model = _load_whisper()
        except Exception as e:
            logger.error("Whisper load error: %s", e)
            return

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm_bytes)
        wav_buffer.seek(0)

        try:
            segments, info = model.transcribe(
                wav_buffer,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                compression_ratio_threshold=2.0,
                log_prob_threshold=-0.8,
                no_speech_threshold=0.7,
                condition_on_previous_text=False,
                without_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                language=None,
            )
            segments_list = list(segments)
            text = " ".join(s.text for s in segments_list).strip().lower()
        except Exception as e:
            logger.error("STT error: %s", e)
            text = ""
            segments_list = []
            info = None

        # Strip punctuation first so filters and wake matching work on plain words
        text = _clean_stt_text(text)

        if not text or len(text) < 2:
            return

        lang_tag = (info.language or "?").lower() if info is not None else "?"
        duration_sec = len(pcm_bytes) / (SAMPLE_RATE * 2)
        is_short = duration_sec < 3.0  # wake phrases are brief; Whisper is uncertain on short clips

        # Reject anything that isn't Spanish or English.
        # For short utterances we only block the language itself, not the probability,
        # because Whisper is inherently unsure about 1-2 word clips.
        if info is not None:
            if lang_tag not in ("es", "en"):
                if not APP_CONFIG.privacy_mode:
                    logger.info(
                        "STT filtered (lang=%s %.0f%%, len=%d)",
                        lang_tag, info.language_probability * 100, len(text),
                    )
                    logger.debug("  filtered text: %s", text[:50])
                return
            if not is_short and info.language_probability < 0.55:
                if not APP_CONFIG.privacy_mode:
                    logger.info(
                        "STT filtered (lang=%s low %.0f%%, len=%d)",
                        lang_tag, info.language_probability * 100, len(text),
                    )
                    logger.debug("  filtered text: %s", text[:50])
                return

        # Reject non-latin scripts (cyrillic, cjk, arabic, etc.) — common false-positive source
        if not _LATIN_SCRIPT_RE.match(text):
            if not APP_CONFIG.privacy_mode:
                logger.debug("STT filtered (non-latin script, lang=%s): %s", lang_tag, text[:50])
            return

        # Detect wake word early so quality filters don't drop explicit triggers
        has_wake, _ = _check_wake(text)

        # Confidence / quality filters: skip if the user explicitly said the wake word
        if not has_wake:
            if segments_list:
                avg_logprob = sum(s.avg_logprob for s in segments_list) / len(segments_list)
                no_speech_prob = max(s.no_speech_prob for s in segments_list)
                # VAD-filtered audio from Whisper can have lower logprobs due to cuts
                avg_threshold = -1.5 if is_short else -1.2
                if avg_logprob < avg_threshold:
                    if not APP_CONFIG.privacy_mode:
                        logger.info(
                            "STT filtered (low confidence): avg_logprob=%.2f | lang=%s | len=%d",
                            avg_logprob, lang_tag, len(text),
                        )
                        logger.debug("  filtered text: %s", text[:50])
                    return
                if no_speech_prob > 0.6:
                    if not APP_CONFIG.privacy_mode:
                        logger.info(
                            "STT filtered (no speech): no_speech_prob=%.2f | lang=%s | len=%d",
                            no_speech_prob, lang_tag, len(text),
                        )
                        logger.debug("  filtered text: %s", text[:50])
                    return

            # Junk filter: repetitions like "mmmmm", "aaah", bare punctuation
            if _JUNK_RE.match(text):
                if not APP_CONFIG.privacy_mode:
                    logger.debug("STT filtered (junk): %s", text)
                return

            # Low entropy filter: keep very short valid words
            if len(set(text.replace(" ", ""))) <= 2 and len(text) < 4:
                if not APP_CONFIG.privacy_mode:
                    logger.debug("STT filtered (low entropy): %s", text)
                return

            # Hallucination loop filter: any n-gram repeated ≥3× covering ≥60% of the text
            if _is_hallucination_loop(text):
                if not APP_CONFIG.privacy_mode:
                    logger.debug("STT filtered (loop): %s", text[:80])
                return

        # Long utterances (>5s) are ambient audio — receivers must ignore
        # the wake check on these; we still emit so calibration/log can see them.
        is_long = len(pcm_bytes) > 5 * SAMPLE_RATE * 2
        if not APP_CONFIG.privacy_mode:
            if is_long:
                logger.debug("STT [%s] (long): %s", lang_tag, text)
            else:
                logger.debug("STT [%s]: %s", lang_tag, text)
        self.utterance_detected.emit(text, is_long, lang_tag)

    def _local_tts_cache_path(self, text: str) -> Path:
        key = hashlib.md5(f"local|{text}".encode("utf-8")).hexdigest()
        return self._tts_cache_dir / f"{key}.wav"

    def _tts_cache_path(self, text: str) -> Path:
        key = hashlib.md5(f"{APP_CONFIG.tts_voice}|{APP_CONFIG.tts_rate}|{text}".encode("utf-8")).hexdigest()
        return self._tts_cache_dir / f"{key}.mp3"

    def _jarvis_cache_path(self, text: str) -> Path:
        key = jarvis_effect_hash(
            text,
            APP_CONFIG.tts_voice,
            APP_CONFIG.tts_rate,
            APP_CONFIG.jarvis_reverb,
            APP_CONFIG.jarvis_delay,
            APP_CONFIG.jarvis_pitch_shift,
            APP_CONFIG.jarvis_compressor,
            APP_CONFIG.jarvis_chorus,
        )
        return self._jarvis_cache_dir / f"{key}.wav"

    def _play_mp3(self, path: Path, blocking: bool = False):
        def _play():
            try:
                snd = pygame.mixer.Sound(str(path))
                channel = snd.play()
                if channel:
                    while channel.get_busy() and self._running:
                        time_mod.sleep(0.05)
            except Exception as e:
                logger.error("mp3 play error: %s", e)
            finally:
                with self._tts_lock:
                    self._tts_busy = False
                try:
                    self.speech_finished.emit()
                except RuntimeError:
                    pass

        with self._tts_lock:
            self._tts_busy = True
        t = threading.Thread(target=_play, daemon=True)
        t.start()
        if blocking:
            t.join()

    def _ensure_local_tts(self):
        if self.tts_local is not None:
            return
        with self._tts_local_lock:
            if self.tts_local is not None:
                return
            engine = pyttsx3.init()
            engine.setProperty("rate", APP_CONFIG.tts_local_rate)
            voices = engine.getProperty("voices")
            for v in voices:
                vname = v.name.lower()
                if any(x in vname for x in ["spanish", "espa", "mexico", "jorge", "carlos", "diego"]):
                    engine.setProperty("voice", v.id)
                    logger.info("Voz local seleccionada: %s", v.name)
                    break
            self.tts_local = engine

    def speak_local(self, text: str, blocking: bool = False):
        try:
            self._ensure_local_tts()
        except Exception as e:
            logger.error("Cannot init local TTS: %s", e)
            return
        cache_path = self._local_tts_cache_path(text)
        if cache_path.exists():
            self._play_mp3(cache_path, blocking=blocking)
            return

        def _speak():
            with self._tts_local_lock:
                try:
                    if sys.platform == "win32":
                        import pythoncom
                        pythoncom.CoInitialize()
                    self.tts_local.save_to_file(text, str(cache_path))
                    self.tts_local.runAndWait()
                    if cache_path.exists():
                        snd = pygame.mixer.Sound(str(cache_path))
                        channel = snd.play()
                        if channel:
                            while channel.get_busy() and self._running:
                                time_mod.sleep(0.05)
                        return
                    # Fallback if save_to_file did not produce a file
                    self.tts_local.say(text)
                    self.tts_local.runAndWait()
                except Exception as e:
                    logger.error("local TTS error: %s", e)
                    try:
                        self.tts_local.say(text)
                        self.tts_local.runAndWait()
                    except Exception:
                        pass
                finally:
                    with self._tts_lock:
                        self._tts_busy = False
                    try:
                        self.speech_finished.emit()
                    except RuntimeError:
                        pass
                    if sys.platform == "win32":
                        try:
                            pythoncom.CoUninitialize()
                        except Exception:
                            pass

        with self._tts_lock:
            self._tts_busy = True
        t = threading.Thread(target=_speak, daemon=True)
        t.start()
        if blocking:
            t.join()

    def speak_wake(self, text: str, blocking: bool = False):
        """Play a wake response using the same voice pipeline as the agent.

        When Jarvis effects are enabled the wake response goes through the
        same effect chain so it sounds identical to the agent's speech.
        Falls back to local TTS if edge-tts is disabled or unavailable.
        """
        if not APP_CONFIG.tts_use_edge:
            self.speak_local(text, blocking=blocking)
            return
        # Delegate to the agent pipeline so effects / voice are consistent
        self.speak_agent(text, blocking=blocking)

    def _generate_wake_cache(self, text: str) -> None:
        """Generate and cache the edge-tts audio for a wake response.

        If Jarvis effects are enabled the processed file is generated too.
        """
        cache_path = self._tts_cache_path(text)
        if not cache_path.exists():
            try:
                _EDGE_TTS.synthesize(
                    text=text,
                    voice=APP_CONFIG.tts_voice,
                    rate=APP_CONFIG.tts_rate,
                    output_path=cache_path,
                    timeout=20.0,
                )
                if not APP_CONFIG.privacy_mode:
                    logger.debug("Wake TTS cached: %s", text[:40])
            except Exception as e:
                logger.warning("Wake TTS cache generation failed: %s", e)
                return

        if APP_CONFIG.jarvis_voice_effects:
            jarvis_path = self._jarvis_cache_path(text)
            jarvis_key = jarvis_path.name
            if jarvis_key in self._jarvis_failed:
                return
            if not jarvis_path.exists():
                try:
                    process_audio_jarvis(
                        input_path=cache_path,
                        output_path=jarvis_path,
                        reverb=APP_CONFIG.jarvis_reverb,
                        delay=APP_CONFIG.jarvis_delay,
                        pitch_shift=APP_CONFIG.jarvis_pitch_shift,
                        compressor=APP_CONFIG.jarvis_compressor,
                        chorus=APP_CONFIG.jarvis_chorus,
                    )
                    if not APP_CONFIG.privacy_mode:
                        logger.debug("Wake Jarvis cache generated: %s", text[:40])
                except Exception as e:
                    logger.warning("Wake Jarvis cache generation failed: %s", e)
                    self._jarvis_failed.add(jarvis_key)

    def prewarm_tts(self, texts: list) -> None:
        """Pre-generate edge-tts and Jarvis-effect cache for wake responses."""
        def _run():
            for t in texts:
                self._generate_wake_cache(t)
        threading.Thread(target=_run, daemon=True, name="tts-prewarm").start()

    def speak_agent(self, text: str, blocking: bool = False):
        if not APP_CONFIG.tts_use_edge:
            self.speak_local(text, blocking=blocking)
            return

        # Check Jarvis-processed cache first
        if APP_CONFIG.jarvis_voice_effects:
            jarvis_path = self._jarvis_cache_path(text)
            if jarvis_path.exists():
                self._play_mp3(jarvis_path, blocking=blocking)
                return
            # If effects previously failed, fall back to raw cache
            if self._jarvis_failed and jarvis_path.name in self._jarvis_failed:
                cache_path = self._tts_cache_path(text)
                if cache_path.exists():
                    self._play_mp3(cache_path, blocking=blocking)
                    return

        # Check raw edge-tts cache
        cache_path = self._tts_cache_path(text)
        if cache_path.exists() and not APP_CONFIG.jarvis_voice_effects:
            self._play_mp3(cache_path, blocking=blocking)
            return

        def _speak():
            try:
                if not cache_path.exists():
                    _EDGE_TTS.synthesize(
                        text=text,
                        voice=APP_CONFIG.tts_voice,
                        rate=APP_CONFIG.tts_rate,
                        output_path=cache_path,
                        timeout=20.0,
                    )

                final_path = cache_path
                # Apply Jarvis effects if enabled
                if APP_CONFIG.jarvis_voice_effects:
                    jarvis_path = self._jarvis_cache_path(text)
                    jkey = jarvis_path.name
                    if jkey not in self._jarvis_failed and not jarvis_path.exists():
                        try:
                            process_audio_jarvis(
                                input_path=cache_path,
                                output_path=jarvis_path,
                                reverb=APP_CONFIG.jarvis_reverb,
                                delay=APP_CONFIG.jarvis_delay,
                                pitch_shift=APP_CONFIG.jarvis_pitch_shift,
                                compressor=APP_CONFIG.jarvis_compressor,
                                chorus=APP_CONFIG.jarvis_chorus,
                            )
                        except Exception as e:
                            logger.warning("Jarvis effects failed (%s), using raw TTS", e)
                            self._jarvis_failed.add(jkey)
                    if jarvis_path.exists():
                        final_path = jarvis_path

                snd = pygame.mixer.Sound(str(final_path))
                channel = snd.play()
                if channel:
                    while channel.get_busy() and self._running:
                        time_mod.sleep(0.05)
            except Exception as e:
                logger.error("edge-tts error: %s", e)
                self.speak_local(text, blocking=True)
            finally:
                with self._tts_lock:
                    self._tts_busy = False
                try:
                    self.speech_finished.emit()
                except RuntimeError:
                    pass

        with self._tts_lock:
            self._tts_busy = True
        t = threading.Thread(target=_speak, daemon=True)
        t.start()
        if blocking:
            t.join()

    def is_speaking(self) -> bool:
        # Only TTS — UI sounds (listen_on/off, ready) share the mixer and
        # would otherwise keep this True after the agent finished talking.
        with self._tts_lock:
            return self._tts_busy

    def stop_speaking(self):
        with self._tts_lock:
            self._tts_busy = False
        try:
            pygame.mixer.stop()
        except Exception:
            pass
        try:
            with self._tts_local_lock:
                if self.tts_local is not None:
                    self.tts_local.stop()
        except Exception:
            pass
