"""Tests for audio_engine wake-word and quality-filter logic."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from audio_engine import _check_wake, _is_hallucination_loop, _LATIN_SCRIPT_RE


def test_check_wake_basic():
    has_wake, clean = _check_wake("oye ghost qué hora es")
    assert has_wake is True
    assert "qué" in clean
    assert "hora" in clean


def test_check_wake_no_wake():
    has_wake, clean = _check_wake("la mesa es de madera")
    assert has_wake is False


def test_check_wake_multi_occurrence_removed():
    # All occurrences of the wake word should be stripped
    has_wake, clean = _check_wake("ghost dime la hora ghost")
    assert has_wake is True
    assert "ghost" not in clean


def test_check_wake_case_insensitive():
    has_wake, clean = _check_wake("Oye GHOST activa luces")
    assert has_wake is True
    assert "activa" in clean


def test_check_wake_multi_word_phrase():
    has_wake, clean = _check_wake("oye ghost enciende la luz")
    assert has_wake is True
    assert "enciende" in clean
    assert "oye" not in clean
    assert "ghost" not in clean


def test_check_wake_single_word_boundary():
    # "ghost" inside "ghostbuster" should NOT match
    has_wake, clean = _check_wake("ghostbuster es una película")
    assert has_wake is False


def test_check_wake_with_punctuation():
    has_wake, clean = _check_wake("ghost, dime la hora")
    assert has_wake is True
    assert "dime" in clean


def test_hallucination_loop_detected():
    text = "vamos a ver si vamos a ver si vamos a ver si funciona"
    assert _is_hallucination_loop(text) is True


def test_hallucination_loop_short_safe():
    text = "hola ghost"
    assert _is_hallucination_loop(text) is False


def test_hallucination_loop_exactly_3_repeats():
    text = "uno dos tres uno dos tres uno dos tres"
    assert _is_hallucination_loop(text) is True


def test_hallucination_loop_2_repeats_safe():
    text = "uno dos tres uno dos tres"
    assert _is_hallucination_loop(text) is False


def test_latin_script_re_accepts_valid():
    assert _LATIN_SCRIPT_RE.match("Hola cómo estás 123") is not None
    assert _LATIN_SCRIPT_RE.match("ÁÉÍÓÚÜÑ áéíóúüñ") is not None
    assert _LATIN_SCRIPT_RE.match("Hello World") is not None


def test_latin_script_re_rejects_invalid():
    assert _LATIN_SCRIPT_RE.match("こんにちは") is None
    assert _LATIN_SCRIPT_RE.match("Привет") is None
    assert _LATIN_SCRIPT_RE.match("你好") is None
