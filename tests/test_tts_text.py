"""Tests for tts_text: markdown sanitizer + incremental sentence splitter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tts_text import sanitize_for_speech, SentenceStream


# --- sanitize_for_speech -----------------------------------------------------

def test_sanitize_strips_markdown():
    out = sanitize_for_speech("## Título\n\nTodo **operativo**, *señor*. Usa `openclaw status`.")
    assert "#" not in out and "*" not in out and "`" not in out
    assert "Todo operativo, señor." in out
    assert "openclaw status" in out


def test_sanitize_links_and_urls():
    out = sanitize_for_speech("Repo en [GitHub](https://github.com/x/y) y docs en https://docs.openclaw.dev/setup")
    assert "GitHub" in out
    assert "https" not in out
    assert "docs.openclaw.dev" in out


def test_sanitize_code_fence_block_omitted():
    out = sanitize_for_speech("Mira:\n```python\ndef foo():\n    return 42\n```\nListo.")
    assert "Bloque de código omitido" in out
    assert "def foo" not in out


def test_sanitize_code_fence_oneliner_kept():
    out = sanitize_for_speech("Corre ```npm install``` y ya.")
    assert "npm install" in out
    assert "```" not in out


def test_sanitize_bullets_and_emoji():
    out = sanitize_for_speech("- punto uno 🚀\n- punto dos ✅\n1. tres")
    assert "🚀" not in out and "✅" not in out
    assert "-" not in out.split()  # bullet markers gone
    assert "punto uno" in out and "tres" in out


def test_sanitize_empty():
    assert sanitize_for_speech("") == ""
    assert sanitize_for_speech(None) == ""


# --- SentenceStream ------------------------------------------------------------

def test_stream_basic_sentences_across_fragments():
    ss = SentenceStream()
    got = []
    for frag in ["Hola, se", "ñor. Los sistemas est", "án en línea. ¿Desea un re", "porte completo?"]:
        got += ss.feed(frag)
    got += ss.flush()
    assert got == ["Hola, señor.", "Los sistemas están en línea.", "¿Desea un reporte completo?"]


def test_stream_holds_open_code_fence():
    ss = SentenceStream()
    assert ss.feed("Mira: ```py\nx = 1\n") == []      # fence open → hold
    out = ss.feed("``` listo. Fin del asunto.")
    out += ss.flush()
    joined = " ".join(out)
    assert "Fin del asunto." in joined


def test_stream_abbreviation_keeps_order():
    ss = SentenceStream()
    got = ss.feed("El Dr. García llegó a las 3. Saludos cordiales. ")
    got += ss.flush()
    # The abbreviation must not split, and order must be preserved
    assert got[0].startswith("El Dr. García")
    assert got == ["El Dr. García llegó a las 3.", "Saludos cordiales."]


def test_stream_merges_short_crumbs():
    ss = SentenceStream()
    got = ss.feed("Sí. Claro que puedo ayudarte con eso ahora mismo. ")
    got += ss.flush()
    # "Sí." alone is below the emit threshold → merged forward
    assert got[0].startswith("Sí. Claro")


def test_stream_paragraph_break_emits():
    ss = SentenceStream()
    got = ss.feed("Primera línea sin punto final\n\nSegunda parte. ")
    got += ss.flush()
    assert any("Primera línea" in s for s in got)
    assert any("Segunda parte." in s for s in got)


def test_stream_flush_returns_tail():
    ss = SentenceStream()
    assert ss.feed("Respuesta corta sin puntuación final") == []
    assert ss.flush() == ["Respuesta corta sin puntuación final"]
    assert ss.flush() == []
