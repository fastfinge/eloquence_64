"""Lightweight IPC helpers for the Eloquence host communication."""
from __future__ import annotations

import pickle
import socket
import struct
import threading
from typing import Any, Tuple

_HEADER_STRUCT = struct.Struct("!I")


class IpcConnection:
    """Simple length-prefixed message channel built on sockets."""

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._send_lock = threading.Lock()

    def send(self, payload: Any) -> None:
        data = pickle.dumps(payload, protocol=4)
        header = _HEADER_STRUCT.pack(len(data))
        with self._send_lock:
            self._sock.sendall(header)
            self._sock.sendall(data)

    def recv(self) -> Any:
        header = _recv_exact(self._sock, _HEADER_STRUCT.size)
        if not header:
            raise EOFError
        (length,) = _HEADER_STRUCT.unpack(header)
        data = _recv_exact(self._sock, length)
        return pickle.loads(data)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


def create_listener() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def accept_authenticated(listener: socket.socket, authkey: bytes) -> IpcConnection:
    conn, _ = listener.accept()
    _authenticate_server(conn, authkey)
    return IpcConnection(conn)


def connect_to_listener(address: Tuple[str, int], authkey: bytes) -> IpcConnection:
    sock = socket.create_connection(address)
    _send_all(sock, authkey)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return IpcConnection(sock)


def _authenticate_server(sock: socket.socket, authkey: bytes) -> None:
    data = _recv_exact(sock, len(authkey))
    if data != authkey:
        sock.close()
        raise ConnectionError("authentication failed")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    remaining = length
    chunks = []
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_all(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)

