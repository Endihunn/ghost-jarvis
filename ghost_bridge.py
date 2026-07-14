"""Bridge to Ghost agent via OpenClaw Gateway WebSocket (Protocol 4).

Auth flow (Protocol 4 trusted backend-client on loopback):
  - HTTP Bearer token (gateway_token) in WS upgrade header
  - connect.challenge nonce received and acknowledged
  - connect req with client.mode="backend" + shared token → ok (no device
    signature; the gateway grants the requested operator scopes from the
    token alone over the loopback bind)
  - chat.send + streaming chat events (state: delta/final)

One in-flight request at a time (matches ghost-jarvis usage pattern).
Auto-detects disconnection in recv loop; next send_message reconnects.
"""
import base64
import fnmatch
import json
import logging
import os
import socket
import struct
import threading
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QThread, QObject, pyqtSignal

from config import APP_CONFIG

logger = logging.getLogger("ghost")


class CancelledError(Exception):
    """Raised when a Ghost request is cancelled by the user."""
    pass

# ---------------------------------------------------------------------------
# HTTP health check
# ---------------------------------------------------------------------------

def _gateway_http_base() -> str:
    from urllib.parse import urlparse
    parsed = urlparse(APP_CONFIG.gateway_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    netloc = parsed.netloc or parsed.path or "127.0.0.1:18789"
    return f"{scheme}://{netloc}"


def _http_is_alive(timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(
            _gateway_http_base().rstrip("/") + "/health",
            headers={"Authorization": f"Bearer {APP_CONFIG.gateway_token}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
            return data.get("ok") is True
    except Exception:
        return False


def is_agent_available(timeout: float = 5.0) -> bool:
    return _http_is_alive(timeout=min(timeout, 3.0))


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_token() -> str:
    return APP_CONFIG.gateway_token or ""


# ---------------------------------------------------------------------------
# Session-key normalization
# ---------------------------------------------------------------------------

def _qualified_session_key(session_key: str, agent_id: str = "main") -> str:
    """Return the fully qualified sessionKey the gateway echoes on chat events.

    The gateway prefixes ``agent:<agentId>:`` to bare keys before echoing
    them back in event payloads. If we register pending entries under the
    bare key while the server reports the prefixed one, the recv loop
    drops every chunk and the send waits until timeout.
    """
    if not session_key:
        return ""
    if ":" in session_key:
        return session_key
    return f"agent:{agent_id}:{session_key}"


# ---------------------------------------------------------------------------
# Question heuristic
# ---------------------------------------------------------------------------

def is_question(text: str) -> bool:
    t = text.strip()
    if "?" in t:
        return True
    interrogatives = [
        "cual ", "cuales ", "cuál ", "cuáles ",
        "qué ", "que ", "quien ", "quienes ", "quién ", "quiénes ",
        "donde ", "dónde ", "cuando ", "cuándo ", "como ", "cómo ",
        "por qué ", "por que ",
        "cuanto ", "cuanta ", "cuantos ", "cuantas ",
        "cuánto ", "cuánta ", "cuántos ", "cuántas ",
        "podrias ", "podrías ", "puedes ", "sabes ", "conoces ",
    ]
    lower = t.lower()
    return any(lower.startswith(i) for i in interrogatives)


# ---------------------------------------------------------------------------
# WebSocket frame primitives (stdlib only)
# ---------------------------------------------------------------------------

def _ws_recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed unexpectedly")
        buf.extend(chunk)
    return bytes(buf)


def _ws_recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    header = _ws_recv_exactly(sock, 2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F

    if length == 126:
        length = struct.unpack(">H", _ws_recv_exactly(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _ws_recv_exactly(sock, 8))[0]

    if masked:
        mask = _ws_recv_exactly(sock, 4)
        payload = _ws_recv_exactly(sock, length)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    else:
        payload = _ws_recv_exactly(sock, length)

    if opcode == 0x08:
        raise ConnectionError("WebSocket closed by server")

    return opcode, payload


def _ws_send_text(sock: socket.socket, text: str, lock: threading.Lock) -> None:
    payload = text.encode("utf-8")
    length = len(payload)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0xFE]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 0xFF]) + struct.pack(">Q", length)

    with lock:
        sock.sendall(header + mask + masked)


def _ws_send_masked_control(sock: socket.socket, opcode: int, payload: bytes,
                             lock: threading.Lock) -> None:
    """Send a client-to-server control frame with the mandatory MASK bit set.

    RFC 6455 §5.1: every frame the client sends MUST be masked, including
    control frames (ping/pong). Sending unmasked frames triggers a server-
    side protocol error ("Invalid WebSocket frame: MASK must be set") and
    the gateway closes the connection, killing whatever run was in flight.
    Control payloads must be ≤125 bytes per spec.
    """
    length = len(payload)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytes([0x80 | (opcode & 0x0F), 0x80 | length])
    with lock:
        sock.sendall(header + mask + masked)


def _ws_pong(sock: socket.socket, payload: bytes, lock: threading.Lock) -> None:
    _ws_send_masked_control(sock, 0x0A, payload, lock)


def _ws_ping(sock: socket.socket, payload: bytes, lock: threading.Lock) -> None:
    _ws_send_masked_control(sock, 0x09, payload, lock)


# ---------------------------------------------------------------------------
# GatewayWS — persistent authenticated WebSocket connection (Protocol 3)
# ---------------------------------------------------------------------------

class GatewayWS:
    """Persistent WebSocket client for the OpenClaw gateway (Protocol 4).

    Uses:
      - Token-only backend-client auth (loopback bind grants operator scopes)
      - chat.send method + chat streaming events
      - TCP keepalive + client-side ping every 30s for half-open detection
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        # Serializes the whole handshake: the monitor supervisor and a
        # GhostWorker can both decide to reconnect at the same time; without
        # this, two sockets get created and one recv loop clobbers the other.
        self._connect_lock = threading.Lock()
        self._connected = False
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._last_recv = 0.0

        # Pending requests: session_key → entry dict
        self._pending: dict[str, dict] = {}
        self._pending_lock = threading.Lock()

        # Monitor mode: qualified session keys we passively read aloud (e.g.
        # "agent:main:main" for the webchat). Their chat finals are delivered
        # to _on_foreign_final instead of a pending request.
        self._monitor_sessions: set[str] = set()
        self._on_foreign_final: Optional[Callable[[str, str], None]] = None
        # Accumulators for monitored sessions' streaming text (cumulative).
        self._foreign_buffers: dict[str, dict] = {}

    def set_monitor(self, sessions: list[str],
                    on_foreign_final: Optional[Callable[[str, str], None]]) -> None:
        """Configure passive read-aloud of other sessions' chat finals.

        `sessions` are raw keys (e.g. ["main"]); they're qualified to
        "agent:<id>:<key>" to match the form the gateway echoes. Call before
        connect() (or reconnect) so the subscriptions are sent on handshake.
        """
        self._monitor_sessions = {_qualified_session_key(s) for s in sessions if s}
        self._on_foreign_final = on_foreign_final

    def _subscribe_monitored(self) -> None:
        """Send chat.subscribe for each monitored session over the live sock."""
        sock = self._sock
        if not sock:
            return
        for sk in self._monitor_sessions:
            # Subscribe by the qualified key; the gateway parses the session
            # out of it and registers this node as a subscriber.
            try:
                _ws_send_text(sock, json.dumps({
                    "type": "req", "id": str(uuid.uuid4()),
                    "method": "chat.subscribe",
                    "params": {"sessionKey": sk},
                }), self._send_lock)
                logger.info("Subscribed to session for read-aloud: %s", sk)
            except Exception as e:
                logger.warning("chat.subscribe failed for %s: %s", sk, e)

    def connect(self, timeout: float = 60.0) -> bool:
        with self._connect_lock:
            return self._connect_locked(timeout)

    def _connect_locked(self, timeout: float = 60.0) -> bool:
        with self._state_lock:
            if self._connected:
                return True
            # Defense-in-depth: if a previous socket was left dangling because
            # close() wasn't called between failures, drop it now so the
            # gateway doesn't see two FDs from the same client.
            stale, self._sock = self._sock, None
        if stale:
            try:
                stale.close()
            except Exception:
                pass

        url = APP_CONFIG.gateway_url
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        token = _get_token()

        sock = None
        try:
            # Timeout finito SOLO para el handshake; tras conectar, el recv
            # loop baja a settimeout(65) + guard de idle de 90s. Un handshake
            # mudo aborta aquí y el supervisor reintenta con backoff.
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.settimeout(timeout)

            # HTTP Upgrade
            ws_key = base64.b64encode(uuid.uuid4().bytes).decode()
            sock.sendall((
                f"GET / HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"Authorization: Bearer {token}\r\n"
                f"\r\n"
            ).encode())

            http_resp = b""
            while b"\r\n\r\n" not in http_resp:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Closed during HTTP upgrade")
                http_resp += chunk
            if b"101" not in http_resp:
                raise ConnectionError(f"WS upgrade failed: {http_resp[:120]!r}")

            # Wait for connect.challenge (get nonce)
            nonce = str(uuid.uuid4())
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                opcode, frame = _ws_recv_frame(sock)
                if opcode == 0x09:
                    _ws_pong(sock, frame, self._send_lock)
                    continue
                if opcode not in (0x01, 0x02):
                    continue
                try:
                    msg = json.loads(frame)
                except Exception:
                    continue
                if msg.get("event") == "connect.challenge":
                    nonce = msg.get("payload", {}).get("nonce", nonce)
                    break

            # Trusted backend client on direct loopback: the gateway grants the
            # requested operator scopes from the shared token alone and the device
            # block may be omitted (gateway protocol v4). We deliberately do NOT
            # attach a signed `device` object here: its signature payload would
            # have to match client.id/mode/scopes exactly, and any drift yields
            # DEVICE_AUTH_SIGNATURE_INVALID. Omitting it keeps the loopback path
            # robust against device-auth.json drift.
            connect_id = str(uuid.uuid4())
            connect_params: dict = {
                "minProtocol": 4, "maxProtocol": 4,
                "client": {
                    "id": "gateway-client", "version": "1.0",
                    "platform": "win32", "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.read", "operator.write", "operator.admin"],
                "auth": {"token": token},
            }

            _ws_send_text(sock, json.dumps({
                "type": "req", "id": connect_id, "method": "connect",
                "params": connect_params,
            }), self._send_lock)

            # Wait for hello-ok (res ok=true)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                opcode, frame = _ws_recv_frame(sock)
                if opcode == 0x09:
                    _ws_pong(sock, frame, self._send_lock)
                    continue
                if opcode not in (0x01, 0x02):
                    continue
                try:
                    msg = json.loads(frame)
                except Exception:
                    continue
                if msg.get("type") == "res" and msg.get("id") == connect_id:
                    if not msg.get("ok"):
                        raise ConnectionError(f"Auth rejected: {msg.get('error')}")
                    break
            else:
                raise ConnectionError("Auth handshake timed out")

            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.settimeout(65.0)
            self._last_recv = time.monotonic()
            with self._state_lock:
                self._sock = sock
                self._connected = True
                self._running = True

            self._recv_thread = threading.Thread(
                target=self._recv_loop, daemon=True, name="gw-recv"
            )
            self._recv_thread.start()

            self._ping_thread = threading.Thread(
                target=self._ping_loop, daemon=True, name="gw-ping"
            )
            self._ping_thread.start()
            logger.info("Gateway WS connected (protocol 4) to %s:%s", host, port)
            # Re-arm read-aloud subscriptions on every (re)connect.
            if self._monitor_sessions:
                self._subscribe_monitored()
            return True

        except Exception as e:
            logger.error("Gateway connect failed: %s", e)
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            return False

    def is_connected(self) -> bool:
        return self._connected

    def mark_disconnected(self) -> None:
        with self._state_lock:
            self._connected = False

    def send_message(
        self,
        message: str,
        agent_id: str = "main",
        session_key: str = "",
        timeout: float = 0.0,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Send a chat message and wait for the final reply.

        `on_delta`, when given, is invoked from the recv thread with each NEW
        text fragment as the agent streams (the gateway sends cumulative
        snapshots; only the unseen suffix is delivered). Exceptions from the
        callback are swallowed so they can't kill the recv loop.

        Espera con watchdog de INACTIVIDAD (config.send_inactivity_timeout):
        mientras lleguen deltas la operación puede durar lo que sea; solo un
        silencio total prolongado aborta. `timeout` > 0 añade además un tope
        total opcional; 0 = sin tope.
        """
        if not self._connected:
            raise ConnectionError("Not connected to gateway")

        # Gateway echoes the key prefixed with "agent:<agentId>:"; align so
        # _pending lookups in _recv_loop match. See _qualified_session_key.
        session_key = _qualified_session_key(session_key, agent_id)

        run_id = str(uuid.uuid4())   # idempotencyKey for the chat run
        req_id = str(uuid.uuid4())   # ID for the chat.send res frame

        done = threading.Event()
        entry: dict = {
            "event": done,
            "chunks": [],
            "received_chars": 0,
            "ok": None,
            "error": None,
            "cancelled": False,
            "req_id": req_id,   # match the chat.send res (ok/err)
            "on_delta": on_delta,
            "last_activity": time.monotonic(),
        }

        with self._pending_lock:
            self._pending[session_key] = entry

        params: dict = {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": run_id,
        }

        try:
            _ws_send_text(self._sock, json.dumps({
                "type": "req", "id": req_id, "method": "chat.send", "params": params,
            }), self._send_lock)
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(session_key, None)
            raise ConnectionError(f"Send failed: {e}") from e

        inactivity = float(getattr(APP_CONFIG, "send_inactivity_timeout", 300) or 0)
        total_cap = timeout if timeout and timeout != float('inf') else 0.0
        started = time.monotonic()
        while not done.wait(timeout=15.0):
            now = time.monotonic()
            idle = now - entry["last_activity"]
            if inactivity and idle > inactivity:
                with self._pending_lock:
                    self._pending.pop(session_key, None)
                raise TimeoutError(
                    f"Agente sin actividad por {idle:.0f}s (watchdog)"
                )
            if total_cap and now - started > total_cap:
                with self._pending_lock:
                    self._pending.pop(session_key, None)
                raise TimeoutError(f"Tope total de {total_cap:.0f}s excedido")

        with self._pending_lock:
            entry = self._pending.pop(session_key, entry)

        if entry.get("cancelled"):
            raise CancelledError("Cancelled by user")

        if not entry["ok"]:
            raise Exception(entry["error"] or "Agent returned an error")

        return "".join(entry["chunks"])

    def cancel(self, session_key: str) -> None:
        """Mark the in-flight request for `session_key` as cancelled and unblock its waiter.

        Late-arriving deltas/finals from the gateway are dropped because the
        entry is removed from `_pending` once `send_message` returns.
        """
        with self._pending_lock:
            entry = self._pending.get(session_key)
            if entry and not entry.get("cancelled"):
                entry["cancelled"] = True
                entry["event"].set()

    def _ping_loop(self) -> None:
        # 10s instead of 30s so the gateway sees client activity while the
        # agent is still "thinking" — the server's idle-cutter closes
        # otherwise-quiet sockets around the 25s mark, which would tank
        # long-running chat runs that legitimately take minutes.
        while self._running:
            time.sleep(10.0)
            with self._state_lock:
                sock = self._sock
                if not sock or not self._connected:
                    continue
            try:
                _ws_ping(sock, b"", self._send_lock)
            except Exception:
                break

    def _recv_loop(self) -> None:
        sock = self._sock
        logger.debug("Gateway recv loop running (protocol 3)")
        try:
            while self._running:
                try:
                    opcode, payload = _ws_recv_frame(sock)
                    self._last_recv = time.monotonic()
                except ConnectionError as e:
                    logger.warning("Gateway connection lost: %s", e)
                    break
                except socket.timeout:
                    idle = time.monotonic() - self._last_recv
                    if idle > 90.0:
                        logger.warning("Gateway idle timeout (%.0fs)", idle)
                        break
                    continue
                except Exception as e:
                    logger.error("Gateway recv error: %s", e)
                    break

                if opcode == 0x09:
                    _ws_pong(sock, payload, self._send_lock)
                    continue
                if opcode == 0x0A:
                    # Pong from server (response to our ping)
                    continue
                if opcode not in (0x01, 0x02):
                    continue

                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")
                mevent = msg.get("event", "")

                # chat.send acknowledgement (ok=true → wait for chat events; ok=false → error)
                if mtype == "res":
                    mid = msg.get("id", "")
                    if not msg.get("ok"):
                        err = msg.get("error") or {}
                        err_msg = err.get("message") if isinstance(err, dict) else str(err)
                        with self._pending_lock:
                            for entry in self._pending.values():
                                if entry.get("req_id") == mid:
                                    entry["ok"] = False
                                    entry["error"] = err_msg
                                    entry["event"].set()
                                    break
                    # ok=true: just the send acknowledgement, keep waiting for chat events
                    continue

                # Streaming chat events
                if mtype == "event" and mevent == "chat":
                    ep = msg.get("payload") or {}
                    sk = ep.get("sessionKey", "")
                    state = ep.get("state", "")
                    run_id = ep.get("runId", "")
                    message_data = ep.get("message") or {}

                    with self._pending_lock:
                        entry = self._pending.get(sk)

                    if not entry:
                        # Sesiones ajenas: se leen en voz alta según la
                        # política (denylist configurable) de _handle_foreign_event.
                        self._handle_foreign_event(sk, run_id, state, message_data)
                        continue

                    # Cualquier evento de la sesión cuenta como actividad
                    # para el watchdog de send_message.
                    entry["last_activity"] = time.monotonic()

                    # Accumulate text from delta/final
                    if state in ("delta", "final") and message_data:
                        content = message_data.get("content") or []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                full_text = block.get("text", "")
                                # Deliver only new characters (server sends cumulative text)
                                sent = entry["received_chars"]
                                if len(full_text) > sent:
                                    fragment = full_text[sent:]
                                    entry["chunks"].append(fragment)
                                    entry["received_chars"] = len(full_text)
                                    cb = entry.get("on_delta")
                                    if cb and not entry.get("cancelled"):
                                        try:
                                            cb(fragment)
                                        except Exception as e:
                                            logger.error("on_delta callback error: %s", e)

                    if state == "final":
                        entry["ok"] = True
                        entry["event"].set()
                    elif state == "aborted":
                        entry["ok"] = False
                        entry["error"] = "Agent run aborted"
                        entry["event"].set()
                    elif state == "error":
                        entry["ok"] = False
                        entry["error"] = "Agent returned error state"
                        entry["event"].set()

        finally:
            with self._state_lock:
                self._connected = False
                self._running = False
                # Close the socket on our side too. Without this the OS keeps
                # the FD half-open until GC, and the gateway sees a zombie
                # ESTABLISHED conn from the same client — the next connect()
                # may be rejected with "WebSocket closed by server" mid-handshake.
                sock, self._sock = self._sock, None
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            with self._pending_lock:
                for entry in self._pending.values():
                    if entry["ok"] is None:
                        entry["ok"] = False
                        entry["error"] = "Gateway connection lost"
                        entry["event"].set()
            # Drop any half-streamed monitor buffers; their runs died with the
            # socket and would otherwise poison the next run keyed the same way.
            self._foreign_buffers.clear()
            logger.warning("Gateway WS recv loop exited")

    def _foreign_session_allowed(self, sk: str) -> bool:
        """Política de lectura en voz alta para sesiones ajenas.

        - read_all_responses=False → solo las sesiones monitoreadas explícitas
          (comportamiento estricto original).
        - read_all_responses=True → todas las sesiones interactivas, EXCEPTO
          las que matcheen voice_session_denylist (crons, heartbeats, runs
          isolated del gateway), fnmatch case-insensitive.
        """
        if not APP_CONFIG.read_all_responses:
            return sk in self._monitor_sessions
        key = (sk or "").lower()
        for pat in getattr(APP_CONFIG, "voice_session_denylist", []) or []:
            if fnmatch.fnmatch(key, pat.lower()):
                logger.info("read-aloud omitido (denylist %r): %s", pat, sk)
                return False
        return True

    def _handle_foreign_event(self, sk: str, run_id: str, state: str,
                              message_data: dict) -> None:
        """Accumulate a monitored session's streaming text and fire the
        read-aloud callback on `final`.

        Buffers are keyed by runId, NOT by sessionKey: the webchat can produce
        several runs on the same session (e.g. two messages stacked after a
        gateway restart), and they may stream interleaved. Keying by session
        let one run's cumulative text clobber the other under the >= guard, so
        a run's final could deliver the wrong/empty text and one reply was
        lost. Per-run buffers keep them independent.
        """
        if not self._foreign_session_allowed(sk):
            return

        key = run_id or sk
        buf = self._foreign_buffers.get(key)
        if buf is None:
            buf = {"text": "", "sk": sk}
            self._foreign_buffers[key] = buf

        if state in ("delta", "final") and message_data:
            for block in message_data.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    full_text = block.get("text", "")
                    if len(full_text) >= len(buf["text"]):
                        buf["text"] = full_text

        if state in ("final", "aborted", "error"):
            text = buf.get("text", "").strip()
            self._foreign_buffers.pop(key, None)
            if state == "final" and text and self._on_foreign_final:
                try:
                    self._on_foreign_final(text, sk)
                except Exception as e:
                    logger.error("foreign-final callback error: %s", e)

    def close(self) -> None:
        with self._state_lock:
            self._running = False
            self._connected = False
            sock, self._sock = self._sock, None
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        ping_thread, self._ping_thread = self._ping_thread, None
        if ping_thread and ping_thread.is_alive():
            ping_thread.join(timeout=1.0)
        logger.info("Gateway WS closed")


# Module-level singleton
_gateway = GatewayWS()


# ---------------------------------------------------------------------------
# GhostWorker — QThread that sends one message and emits the response
# ---------------------------------------------------------------------------

class GhostWorker(QThread):
    """Sends one prompt and streams the reply.

    Signals (all delivered queued onto the GUI thread):
      sentence_ready(str)      — a complete, speech-sanitized sentence arrived.
                                 Emitted as the agent streams, so TTS can start
                                 speaking long before the run finishes.
      stream_done(str, bool)   — full sanitized text + is_question. Always
                                 emitted on success, even if no sentences were
                                 (streaming disabled or empty reply).
      error_occurred(str)      — terminal failure; no stream_done follows.
    """
    sentence_ready = pyqtSignal(str)
    stream_done = pyqtSignal(str, bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, prompt: str, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        global _gateway
        from tts_text import SentenceStream, sanitize_for_speech

        if not _gateway.is_connected():
            logger.info("GhostWorker: gateway not connected, attempting reconnect...")
            if not _gateway.connect(timeout=30.0):
                self.error_occurred.emit("no encontrado")
                return

        if self._cancelled:
            return

        prompt = APP_CONFIG.ghost_prompt_prefix + self.prompt
        start = time.time()
        first_sentence_at: list[float] = []
        if not APP_CONFIG.privacy_mode:
            logger.info("Sending to Ghost: %s...", prompt[:80])

        splitter = SentenceStream()

        def _emit_sentences(sentences: list[str]) -> None:
            for s in sentences:
                clean = sanitize_for_speech(s)
                if clean and not self._cancelled:
                    if not first_sentence_at:
                        first_sentence_at.append(time.time() - start)
                    self.sentence_ready.emit(clean)

        def _on_delta(fragment: str) -> None:
            # Runs on the gateway recv thread; signal emission is queued so the
            # GUI thread does the actual TTS work.
            if self._cancelled or not APP_CONFIG.streaming_tts:
                return
            _emit_sentences(splitter.feed(fragment))

        try:
            text = _gateway.send_message(
                prompt,
                agent_id="main",
                session_key=APP_CONFIG.session_key,
                on_delta=_on_delta,
            )
        except CancelledError:
            logger.info("Ghost request cancelled")
            return
        except TimeoutError:
            elapsed = time.time() - start
            logger.error("Ghost timeout after %.1fs", elapsed)
            self.error_occurred.emit(f"Ghost tardó demasiado en responder. (Timeout: {elapsed:.0f}s)")
            return
        except ConnectionError as e:
            logger.error("Ghost connection error: %s", e)
            _gateway.mark_disconnected()
            self.error_occurred.emit(f"no encontrado: {e}")
            return
        except Exception as e:
            logger.error("Ghost worker error: %s", e)
            self.error_occurred.emit(str(e))
            return

        if self._cancelled:
            return

        # Tail of the stream that never hit a sentence boundary.
        if APP_CONFIG.streaming_tts:
            _emit_sentences(splitter.flush())

        elapsed = time.time() - start
        if not text:
            text = "Ghost no respondió nada."

        question = is_question(text)
        if not APP_CONFIG.privacy_mode:
            ttfs = f", first-sentence={first_sentence_at[0]:.1f}s" if first_sentence_at else ""
            logger.info(
                "Ghost responded in %.1fs (%d chars, question=%s%s)",
                elapsed, len(text), question, ttfs,
            )
        self.stream_done.emit(sanitize_for_speech(text) or text, question)


# ---------------------------------------------------------------------------
# StandbyChecker — non-blocking HTTP health check
# ---------------------------------------------------------------------------

class StandbyChecker(QThread):
    available = pyqtSignal()

    def __init__(self, timeout: float = 3.0, parent=None):
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        try:
            if _http_is_alive(timeout=self._timeout):
                self.available.emit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GhostBridge — public interface used by the main window
# ---------------------------------------------------------------------------

class GhostBridge(QObject):
    # Emitted (from the recv thread, delivered queued to the GUI thread) when a
    # monitored session — e.g. the webchat 'main' — produces a final reply that
    # should be read aloud.
    foreign_response = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._current_worker: Optional[GhostWorker] = None
        self._monitor_running = False
        self._monitor_thread: Optional[threading.Thread] = None

    def is_available(self) -> bool:
        return _http_is_alive(timeout=3.0)

    def is_busy(self) -> bool:
        """True while a prompt is in flight (worker thread running)."""
        return bool(self._current_worker and self._current_worker.isRunning())

    # ── Read-aloud monitor ────────────────────────────────────────────────
    def start_monitor(self, sessions: list[str]) -> None:
        """Keep a persistent gateway connection subscribed to `sessions` and
        emit foreign_response for each final reply, so the app can read other
        sessions (the webchat) aloud. Idempotent.
        """
        if self._monitor_running:
            return
        self._monitor_running = True
        _gateway.set_monitor(sessions, self._on_foreign_final)
        self._monitor_thread = threading.Thread(
            target=self._monitor_supervisor, daemon=True, name="gw-monitor"
        )
        self._monitor_thread.start()
        logger.info("Read-aloud monitor started for sessions: %s", sessions)

    def _on_foreign_final(self, text: str, session_key: str) -> None:
        # Called from the recv thread; pyqtSignal hops to the GUI thread.
        self.foreign_response.emit(text)

    def _monitor_supervisor(self) -> None:
        """Maintain the gateway connection so monitored subscriptions stay live.
        Reconnects with backoff; connect() re-arms the subscriptions.
        """
        backoff = 3.0
        while self._monitor_running:
            if not _gateway.is_connected():
                if _gateway.connect(timeout=30.0):
                    backoff = 3.0
                else:
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 30.0)
                    continue
            time.sleep(3.0)

    def send(
        self,
        prompt: str,
        on_response: Callable,
        on_error: Callable,
        on_sentence: Optional[Callable] = None,
    ) -> None:
        """Send a prompt. `on_response(full_text, is_question)` fires once at
        the end; `on_sentence(text)` fires per streamed sentence (if given)."""
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.cancel()
            _gateway.cancel(_qualified_session_key(APP_CONFIG.session_key))
            self._current_worker.wait(2000)

        worker = GhostWorker(prompt)
        if on_sentence is not None:
            worker.sentence_ready.connect(on_sentence)
        worker.stream_done.connect(on_response)
        worker.error_occurred.connect(on_error)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._current_worker = worker
        worker.start()

    def cancel_current(self) -> None:
        """Cancel the in-flight Ghost request, if any.

        Used when the user says the wake word during PROCESSING to abort the
        current turn. Unblocks the worker's `send_message` wait via the
        gateway's pending entry; late deltas/finals are dropped.
        """
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.cancel()
            _gateway.cancel(_qualified_session_key(APP_CONFIG.session_key))

    def close(self) -> None:
        global _gateway
        self._monitor_running = False
        _gateway.close()

    def _cleanup_worker(self, worker: GhostWorker) -> None:
        if self._current_worker is worker:
            self._current_worker = None
