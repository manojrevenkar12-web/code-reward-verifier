"""Length-prefixed JSON frames over a stream socket. The reading side enforces a size cap and a deadline."""

import json
import os
import select
import socket
import struct
import time
from collections.abc import Callable
from typing import Any

HEADER = struct.Struct(">I")


class FrameError(Exception):
    """Malformed or oversized frame: the peer broke the protocol."""


class PeerClosed(Exception):
    """The peer closed the channel."""


class Deadline(Exception):
    """No complete frame before the deadline."""


def send(sock: socket.socket, message: Any) -> None:
    body = json.dumps(message, separators=(",", ":")).encode()
    sock.sendall(HEADER.pack(len(body)) + body)


def recv_blocking(sock: socket.socket, max_bytes: int = 64 * 1024 * 1024) -> Any:
    """Child side: wait as long as it takes (the parent owns the clock)."""
    head = _read_exact_blocking(sock, HEADER.size)
    (n,) = HEADER.unpack(head)
    if n > max_bytes:
        raise FrameError("frame too large")
    return _parse(_read_exact_blocking(sock, n))


def _read_exact_blocking(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise PeerClosed
        buf += chunk
    return bytes(buf)


def _parse(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as e:
        raise FrameError(f"frame is not JSON: {e}") from None


class Reader:
    """Parent side: reads frames before a deadline while draining a second fd (the child's output) so the
    child never blocks on a full pipe; calls `on_output` with each chunk."""

    def __init__(
        self, sock: socket.socket, output_fd: int | None, on_output: Callable[[bytes], None], max_bytes: int
    ) -> None:
        self.sock = sock
        self.output_fd = output_fd
        self.on_output = on_output
        self.max_bytes = max_bytes
        self.buf = bytearray()

    def recv(self, deadline: float) -> Any:
        while True:
            if len(self.buf) >= HEADER.size:
                (n,) = HEADER.unpack_from(self.buf)
                if n > self.max_bytes:
                    raise FrameError("frame too large")
                if len(self.buf) >= HEADER.size + n:
                    body = bytes(self.buf[HEADER.size : HEADER.size + n])
                    del self.buf[: HEADER.size + n]
                    return _parse(body)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Deadline
            fds: list[Any] = [self.sock]
            if self.output_fd is not None:
                fds.append(self.output_fd)
            ready, _, _ = select.select(fds, [], [], remaining)
            if self.output_fd is not None and self.output_fd in ready:
                chunk = _read_fd(self.output_fd)
                if chunk:
                    self.on_output(chunk)
                else:
                    self.output_fd = None  # output closed; keep reading the channel
            if self.sock in ready:
                chunk = self.sock.recv(1 << 20)
                if not chunk:
                    raise PeerClosed
                self.buf += chunk


def _read_fd(fd: int) -> bytes:
    # select() reported the fd readable, so this read does not block; b"" means the writer closed it.
    return os.read(fd, 1 << 16)
