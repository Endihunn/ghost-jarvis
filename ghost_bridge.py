"""Bridge to Ghost agent via OpenClaw Gateway WebSocket (Protocol 3).

Auth flow:
  - HTTP Bearer token (gateway_token) in WS upgrade header
  - connect.challenge nonce → ED25519-signed device payload → hello-ok
  - chat.send + streaming chat events (state: delta/final)

One in-flight request at a time (matches ghost-jarvis usage pattern).
Auto-detects disconnection in recv loop; next send_message reconnects.
"""
import base64
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

from PyQt6.QtCore import QThread, pyqtSignal

from config import APP_CONFIG

logger = logging.getLogger("ghost")


class CancelledError(Exception):
    """Raised when a Ghost request is cancelled by the user."""
    pass

# ---------------------------------------------------------------------------
# Device identity (loaded once, cached)
# ---------------------------------------------------------------------------

_device_identity: Optional[dict] = None
_device_identity_lock = threading.Lock()


def _load_device_identity() -> Optional[dict]:
    global _device_identity
    if _device_identity is not None:
        return _device_identity
    with _device_identity_lock:
        if _device_identity is not None:
            return _device_identity
        try:
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key, Encoding, PublicFormat
            )
            identity_dir = Path.home() / ".openclaw" / "identity"
            device = json.loads((identity_dir / "device.json").read_text(encoding="utf-8"))
            auth_data = json.loads((identity_dir / "device-auth.json").read_text(encoding="utf-8"))

            private_key = load_pem_private_key(device["privateKeyPem"].encode(), password=None)
            pub_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

            operator_entry = auth_data.get("tokens", {}).get("operator", {})
            _device_identity = {
                "device_id": device["deviceId"],
                "private_key": private_key,
                "pub_key_b64url": base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode(),
                "device_token": operator_entry.get("token", ""),
            }
            logger.info("Device identity loaded: %s...", device["deviceId"][:16])
            return _device_identity
        except Exception as e:
            logger.warning("Device identity unavailable: %s", e)
            return None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _build_signed_device_auth(nonce: str, gateway_token: str) -> Optional[dict]:
    """Build the signed `device` object for the connect params."""
    ident = _load_device_identity()
    if not ident:
        return None
    client_id = "cli"
    client_mode = "cli"
    role = "operator"
    scopes = "operator.admin"
    signed_at_ms = int(time.time() * 1000)
    payload_str = "|".join([
        "v3", ident["device_id"], client_id, client_mode,
        role, scopes, str(signed_at_ms), gateway_token, nonce, "win32", ""
    ])
    sig = ident["private_key"].sign(payload_str.encode("utf-8"))
    return {
        "id": ident["device_id"],
        "publicKey": ident["pub_key_b64url"],
        "signature": _b64url(sig),
        "signedAt": signed_at_ms,
        "nonce": nonce,
    }


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


def _ws_pong(sock: socket.socket, payload: bytes, lock: threading.Lock) -> None:
    with lock:
        sock.sendall(bytes([0x8A, len(payload)]) + payload)


def _ws_ping(sock: socket.socket, payload: bytes, lock: threading.Lock) -> None:
    with lock:
        sock.sendall(bytes([0x89, len(payload)]) + payload)


# ---------------------------------------------------------------------------
# GatewayWS — persistent authenticated WebSocket connection (Protocol 3)
# ---------------------------------------------------------------------------

class GatewayWS:
    """Persistent WebSocket client for the OpenClaw gateway (Protocol 3).

    Uses:
      - ED25519-signed device identity for operator.admin scope
      - chat.send method + chat streaming events
      - TCP keepalive + client-side ping every 30s for half-open detection
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._connected = False
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._last_recv = 0.0

        # Pending requests: session_key → entry dict
        self._pending: dict[str, dict] = {}
        self._pending_lock = threading.Lock()

    def connect(self, timeout: float = 10.0) -> bool:
        with self._state_lock:
            if self._connected:
                return True

        url = APP_CONFIG.gateway_url
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        token = _get_token()

        sock = None
        try:
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

            # Build device auth (ED25519 signature)
            ident = _load_device_identity()
            device_obj = _build_signed_device_auth(nonce, token)

            connect_id = str(uuid.uuid4())
            connect_params: dict = {
                "minProtocol": 3, "maxProtocol": 3,
                "client": {
                    "id": "cli", "version": "1.0",
                    "platform": "win32", "mode": "cli",
                },
                "scopes": ["operator.admin"],
                "auth": {"token": token},
            }
            if ident and device_obj:
                connect_params["auth"]["deviceToken"] = ident["device_token"]
                connect_params["device"] = device_obj

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
            logger.info("Gateway WS connected (protocol 3) to %s:%s", host, port)
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
        timeout: float = 240.0,
    ) -> str:
        if not self._connected:
            raise ConnectionError("Not connected to gateway")

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

        if not done.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(session_key, None)
            raise TimeoutError(f"Agent did not respond within {timeout:.0f}s")

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
        while self._running:
            time.sleep(30.0)
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
                    message_data = ep.get("message") or {}

                    with self._pending_lock:
                        entry = self._pending.get(sk)

                    if not entry:
                        continue

                    # Accumulate text from delta/final
                    if state in ("delta", "final") and message_data:
                        content = message_data.get("content") or []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                full_text = block.get("text", "")
                                # Deliver only new characters (server sends cumulative text)
                                sent = entry["received_chars"]
                                if len(full_text) > sent:
                                    entry["chunks"].append(full_text[sent:])
                                    entry["received_chars"] = len(full_text)

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
            with self._pending_lock:
                for entry in self._pending.values():
                    if entry["ok"] is None:
                        entry["ok"] = False
                        entry["error"] = "Gateway connection lost"
                        entry["event"].set()
            logger.warning("Gateway WS recv loop exited")

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
    response_ready = pyqtSignal(str, bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, prompt: str, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        global _gateway

        if not _gateway.is_connected():
            logger.info("GhostWorker: gateway not connected, attempting reconnect...")
            if not _gateway.connect(timeout=10.0):
                self.error_occurred.emit("no encontrado")
                return

        if self._cancelled:
            return

        prompt = APP_CONFIG.ghost_prompt_prefix + self.prompt
        start = time.time()
        if not APP_CONFIG.privacy_mode:
            logger.info("Sending to Ghost: %s...", prompt[:80])

        try:
            text = _gateway.send_message(
                prompt,
                agent_id="main",
                session_key=APP_CONFIG.session_key,
                timeout=240.0,
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

        elapsed = time.time() - start
        if not text:
            text = "Ghost no respondió nada."

        question = is_question(text)
        if not APP_CONFIG.privacy_mode:
            logger.info("Ghost responded in %.1fs (%d chars, question=%s)", elapsed, len(text), question)
        self.response_ready.emit(text, question)


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

class GhostBridge:
    def __init__(self):
        self._current_worker: Optional[GhostWorker] = None

    def is_available(self) -> bool:
        return _http_is_alive(timeout=3.0)

    def send(self, prompt: str, on_response: Callable, on_error: Callable) -> None:
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.cancel()
            _gateway.cancel(APP_CONFIG.session_key)
            self._current_worker.wait(2000)

        worker = GhostWorker(prompt)
        worker.response_ready.connect(on_response)
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
            _gateway.cancel(APP_CONFIG.session_key)

    def close(self) -> None:
        global _gateway
        _gateway.close()

    def _cleanup_worker(self, worker: GhostWorker) -> None:
        if self._current_worker is worker:
            self._current_worker = None
