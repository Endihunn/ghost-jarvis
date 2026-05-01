"""Tests for ghost_bridge utilities: URL parsing, question heuristic, WS frames."""
import sys
from pathlib import Path
import json
import struct
import socket
import threading

sys.path.insert(0, str(Path(__file__).parent.parent))

import ghost_bridge as gb


def test_is_question():
    assert gb.is_question("¿qué hora es?") is True
    assert gb.is_question("qué hora es") is True
    assert gb.is_question("cuántos años tienes") is True
    assert gb.is_question("podrías ayudarme") is True
    assert gb.is_question("sabes algo") is True
    assert gb.is_question("activa las luces") is False
    assert gb.is_question("ok") is False


def test_is_question_with_accents():
    assert gb.is_question("cómo estás") is True
    assert gb.is_question("dónde queda") is True
    assert gb.is_question("por qué no") is True
    assert gb.is_question("por que no") is True


def test_gateway_http_base_from_ws():
    import config as cm
    orig = cm.APP_CONFIG.gateway_url
    try:
        cm.APP_CONFIG.gateway_url = "ws://localhost:9000"
        assert gb._gateway_http_base() == "http://localhost:9000"
    finally:
        cm.APP_CONFIG.gateway_url = orig


def test_gateway_http_base_from_wss():
    import config as cm
    orig = cm.APP_CONFIG.gateway_url
    try:
        cm.APP_CONFIG.gateway_url = "wss://gw.example.com:443"
        assert gb._gateway_http_base() == "https://gw.example.com:443"
    finally:
        cm.APP_CONFIG.gateway_url = orig


def test_ws_send_text_frame_format():
    """Verify _ws_send_text produces a valid masked text frame."""
    recv_sock, send_sock = socket.socketpair()
    lock = threading.Lock()
    try:
        text = "hola"
        gb._ws_send_text(send_sock, text, lock)

        header = recv_sock.recv(2)
        assert header[0] & 0x0F == 0x01  # text opcode
        masked = bool(header[1] & 0x80)
        assert masked is True
        length = header[1] & 0x7F
        assert length == len(text)
        mask = recv_sock.recv(4)
        payload = recv_sock.recv(length)
        unmasked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        assert unmasked.decode("utf-8") == text
    finally:
        recv_sock.close()
        send_sock.close()


def test_ws_recv_frame_text():
    """Verify _ws_recv_frame parses a text frame correctly."""
    recv_sock, send_sock = socket.socketpair()
    try:
        text = "hello"
        payload = text.encode("utf-8")
        mask = b"\x01\x02\x03\x04"
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
        send_sock.sendall(frame)

        opcode, received = gb._ws_recv_frame(recv_sock)
        assert opcode == 0x01
        assert received.decode("utf-8") == text
    finally:
        recv_sock.close()
        send_sock.close()


def test_ws_recv_frame_close_raises():
    """Verify _ws_recv_frame raises ConnectionError on close frame."""
    recv_sock, send_sock = socket.socketpair()
    try:
        frame = bytes([0x88, 0x02]) + b"\x03\xe8"  # close frame with code 1000
        send_sock.sendall(frame)
        try:
            gb._ws_recv_frame(recv_sock)
            assert False, "Expected ConnectionError"
        except ConnectionError as e:
            assert "closed by server" in str(e)
    finally:
        recv_sock.close()
        send_sock.close()


def test_build_signed_device_auth_without_identity(monkeypatch):
    """If identity can't be loaded, _build_signed_device_auth returns None."""
    monkeypatch.setattr(gb, "_load_device_identity", lambda: None)
    result = gb._build_signed_device_auth("nonce123", "token456")
    assert result is None


def test_b64url():
    assert gb._b64url(b"test") == "dGVzdA"
    assert "=" not in gb._b64url(b"any")
    assert "+" not in gb._b64url(b"\xff\xfe")
    assert "/" not in gb._b64url(b"\xff\xfe")
