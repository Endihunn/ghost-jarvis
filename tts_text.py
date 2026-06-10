"""Text utilities for spoken output.

- sanitize_for_speech(): markdown/URL/emoji cleanup so edge-tts doesn't read
  asterisks, fences or raw links aloud.
- SentenceStream: incremental sentence splitter for streaming TTS — feed()
  arbitrary text fragments as they arrive, get back complete sentences.
"""
import re
from urllib.parse import urlparse

# --- Markdown / noise patterns -------------------------------------------------
# Block fence: the language tag only exists when a newline follows (```python\n…).
_FENCE_BLOCK_RE = re.compile(r"```[\w+-]*\r?\n(.*?)```", re.DOTALL)
# Inline fence (```npm install```): everything inside is content, no lang tag.
_FENCE_INLINE_RE = re.compile(r"```(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_BULLET_RE = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_NUMBULLET_RE = re.compile(r"^\s*\d{1,2}[.)]\s+", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# Emoji & pictographs (BMP ranges + supplementary planes via surrogate-safe classes)
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # symbols, emoticons, transport, supplemental
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"   # flags
    "←-⇿"           # arrows
    "⬀-⯿"           # misc arrows/symbols
    "️‍⃣"      # VS-16, ZWJ, keycap
    "]+",
    flags=re.UNICODE,
)


def _fence_repl(m: re.Match) -> str:
    inner = (m.group(1) or "").strip()
    # A one-liner snippet is fine to read aloud; a real block is not.
    if inner and "\n" not in inner and len(inner) <= 80:
        return f" {inner} "
    return ". Bloque de código omitido. "


def _url_repl(m: re.Match) -> str:
    try:
        netloc = urlparse(m.group(0)).netloc
        return netloc.removeprefix("www.") or "un enlace"
    except Exception:
        return "un enlace"


def sanitize_for_speech(text: str) -> str:
    """Convert agent markdown into plain prose suitable for TTS."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n")
    t = _FENCE_BLOCK_RE.sub(_fence_repl, t)
    t = _FENCE_INLINE_RE.sub(_fence_repl, t)
    t = _IMAGE_RE.sub(lambda m: m.group(1) or "", t)
    t = _LINK_RE.sub(lambda m: m.group(1), t)
    t = _URL_RE.sub(_url_repl, t)
    t = _INLINE_CODE_RE.sub(lambda m: m.group(1), t)
    t = _HEADER_RE.sub("", t)
    # Bold/italic markers can nest (***x***); run twice to unwrap both layers.
    t = _BOLD_ITALIC_RE.sub(lambda m: m.group(2), t)
    t = _BOLD_ITALIC_RE.sub(lambda m: m.group(2), t)
    t = _STRIKE_RE.sub(lambda m: m.group(1), t)
    t = _TABLE_SEP_RE.sub("", t)
    t = t.replace("|", ", ")
    t = _BULLET_RE.sub("", t)
    t = _NUMBULLET_RE.sub("", t)
    t = _QUOTE_RE.sub("", t)
    t = _HTML_TAG_RE.sub("", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "y").replace("&lt;", "<").replace("&gt;", ">")
    t = _EMOJI_RE.sub("", t)
    # Collapse whitespace; keep sentence punctuation intact.
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = t.replace("\n", ". ")
    t = re.sub(r"\s*\.\s*(\.\s*)+", ". ", t)   # ".. . ." → ". "
    t = re.sub(r":\s*\.", ".", t)              # "cosas:." → "cosas."
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    return t.strip()


# --- Incremental sentence splitting ---------------------------------------------

# Split AFTER .!?… when followed by whitespace + something that looks like a new
# sentence start. Conservative on purpose: mid-number dots ("3.14") and common
# abbreviations don't match because the next char isn't an uppercase/¿¡ opener.
_SENT_SPLIT_RE = re.compile(
    r"(?<=[.!?…])[\s]+(?=[¿¡«\"'(\[]?[A-ZÁÉÍÓÚÑÜ0-9])"
)
_ABBREV_TAIL_RE = re.compile(
    r"(?:\b(?:Sr|Sra|Srta|Dr|Dra|Ing|Lic|Prof|etc|vs|p\.ej|No|Núm|art|aprox)\.)$",
    re.IGNORECASE,
)

# Don't emit fragments shorter than this — merge them into the next sentence so
# the TTS pipeline doesn't fire for "Ok." / "Sí." style crumbs mid-stream.
_MIN_EMIT_CHARS = 12


class SentenceStream:
    """Accumulates streamed text and yields complete sentences as they form."""

    def __init__(self):
        self._buf = ""
        self._pending = ""   # short sentence held back to merge with the next

    def feed(self, fragment: str) -> list[str]:
        if not fragment:
            return []
        self._buf += fragment
        # Inside an unclosed code fence: hold everything (the sanitizer needs
        # the closing ``` to collapse the block properly).
        if self._buf.count("```") % 2 == 1:
            return []

        parts = _SENT_SPLIT_RE.split(self._buf)
        if len(parts) <= 1:
            return self._maybe_emit_paragraph()
        # Last part may be an incomplete sentence — keep it buffered.
        self._buf = parts[-1]
        out: list[str] = []
        carry = ""   # abbreviation false-split: fuse with the NEXT part in order
        for p in parts[:-1]:
            p = (carry + " " + p).strip() if carry else p.strip()
            carry = ""
            if not p:
                continue
            if _ABBREV_TAIL_RE.search(p):
                carry = p
                continue
            merged = (self._pending + " " + p).strip() if self._pending else p
            if len(merged) < _MIN_EMIT_CHARS:
                self._pending = merged
                continue
            self._pending = ""
            out.append(merged)
        if carry:
            # Abbreviation ran into the incomplete tail — keep order intact.
            self._buf = carry + " " + self._buf
        return out

    def _maybe_emit_paragraph(self) -> list[str]:
        """Emit on double-newline even without terminal punctuation."""
        if "\n\n" not in self._buf:
            return []
        head, tail = self._buf.rsplit("\n\n", 1)
        head = head.strip()
        self._buf = tail
        if not head:
            return []
        merged = (self._pending + " " + head).strip() if self._pending else head
        if len(merged) < _MIN_EMIT_CHARS:
            self._pending = merged
            return []
        self._pending = ""
        return [merged]

    def flush(self) -> list[str]:
        """Return whatever remains (incomplete sentence + held-back shorts)."""
        rest = (self._pending + " " + self._buf).strip()
        self._pending = ""
        self._buf = ""
        return [rest] if rest else []
