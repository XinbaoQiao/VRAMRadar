from __future__ import annotations

import hmac
import os
import secrets
import socket
import struct
import sys
import threading


ENDPOINT_ENV = "VRAM_RADAR_ASKPASS_ENDPOINT"
NONCE_ENV = "VRAM_RADAR_ASKPASS_NONCE"
MAX_SECRET_BYTES = 16_384


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("password broker closed unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class PasswordBroker:
    """Short-lived loopback broker that keeps the password out of argv and env."""

    def __init__(self, password: str, *, max_requests: int = 3) -> None:
        encoded = password.encode("utf-8")
        if not encoded or len(encoded) > MAX_SECRET_BYTES:
            raise ValueError("password has an invalid encoded length")
        self._password = bytearray(encoded)
        self._max_requests = max_requests
        self._nonce = secrets.token_urlsafe(32)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="vram-radar-askpass", daemon=True)

    @property
    def environment(self) -> dict[str, str]:
        host, port = self._listener.getsockname()
        return {ENDPOINT_ENV: f"{host}:{port}", NONCE_ENV: self._nonce}

    def __enter__(self) -> "PasswordBroker":
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=1)
        for index in range(len(self._password)):
            self._password[index] = 0

    def _serve(self) -> None:
        accepted = 0
        while not self._stop.is_set() and accepted < self._max_requests:
            try:
                connection, _address = self._listener.accept()
            except (OSError, socket.timeout):
                continue
            with connection:
                connection.settimeout(2)
                try:
                    request = bytearray()
                    while len(request) <= 256 and not request.endswith(b"\n"):
                        chunk = connection.recv(64)
                        if not chunk:
                            break
                        request.extend(chunk)
                    supplied = request.rstrip(b"\r\n").decode("ascii", errors="ignore")
                    if not hmac.compare_digest(supplied, self._nonce):
                        continue
                    payload = bytes(self._password)
                    connection.sendall(struct.pack("!I", len(payload)) + payload)
                    accepted += 1
                except (OSError, UnicodeError):
                    continue


def request_password(endpoint: str, nonce: str) -> str:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator or host != "127.0.0.1" or not port_text.isdigit():
        raise RuntimeError("invalid password broker endpoint")
    with socket.create_connection((host, int(port_text)), timeout=3) as connection:
        connection.sendall(nonce.encode("ascii") + b"\n")
        size = struct.unpack("!I", _receive_exact(connection, 4))[0]
        if not 0 < size <= MAX_SECRET_BYTES:
            raise RuntimeError("invalid password broker response")
        return _receive_exact(connection, size).decode("utf-8")


def main() -> int:
    endpoint = os.environ.get(ENDPOINT_ENV, "")
    nonce = os.environ.get(NONCE_ENV, "")
    if not endpoint or not nonce:
        return 1
    try:
        value = request_password(endpoint, nonce)
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return 1
    sys.stdout.write(value)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
