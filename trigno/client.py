"""Command-port client for the Trigno SDK server (Trigno Control Utility)."""

from __future__ import annotations

import logging
import queue
import socket
import threading
from typing import Callable

from . import protocol

log = logging.getLogger(__name__)


class TrignoError(RuntimeError):
    pass


class CommandClient:
    """Sends commands/queries on port 50040 and returns the server's replies.

    A background thread splits the incoming stream on the double CRLF terminator.
    Unsolicited messages (e.g. "STOPPED" after a stop trigger, "SENSOR n PAIR
    COMPLETE") are routed to `on_event` instead of being taken as a reply.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = protocol.COMMAND_PORT,
        timeout: float = 10.0,
        on_event: Callable[[str], None] | None = None,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.on_event = on_event
        self.version = ""
        self._sock: socket.socket | None = None
        self._replies: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()

    def connect(self) -> str:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise TrignoError(
                f"Cannot reach Trigno SDK server at {self.host}:{self.port} ({exc}). "
                "Is the Trigno Control Utility running and reachable from this machine?"
            ) from exc
        self._sock.settimeout(None)
        self._reader = threading.Thread(target=self._read_loop, name="trigno-cmd", daemon=True)
        self._reader.start()
        # The server announces its protocol version on connect (section 6.3.1).
        self.version = self._next_reply()
        log.info("Connected: %s", self.version)
        return self.version

    def close(self) -> None:
        if self._sock is None:
            return
        self._closed.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self._sock = None

    def __enter__(self) -> CommandClient:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def send(self, command: str, timeout: float | None = None) -> str:
        """Send one command or query and return its reply text."""
        if self._sock is None:
            raise TrignoError("Not connected")
        with self._lock:
            log.debug(">> %s", command)
            self._sock.sendall(protocol.encode_command(command))
            reply = self._next_reply(timeout)
        log.debug("<< %s", reply)
        return reply

    def command(self, command: str, timeout: float | None = None) -> str:
        """Send a command that must not be rejected; raises on INVALID/CANNOT replies."""
        reply = self.send(command, timeout)
        if reply in (protocol.REPLY_INVALID, protocol.REPLY_CANNOT):
            raise TrignoError(f"{command!r} -> {reply}")
        return reply

    def _next_reply(self, timeout: float | None = None) -> str:
        try:
            return self._replies.get(timeout=timeout or self.timeout)
        except queue.Empty:
            raise TrignoError("Timed out waiting for reply from Trigno SDK server") from None

    def _read_loop(self) -> None:
        buf = b""
        while not self._closed.is_set():
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while protocol.TERMINATOR in buf:
                raw, buf = buf.split(protocol.TERMINATOR, 1)
                message = raw.decode("ascii", errors="replace").strip()
                if protocol.is_async_message(message):
                    log.info("Server event: %s", message)
                    if self.on_event:
                        self.on_event(message)
                else:
                    self._replies.put(message)
        if not self._closed.is_set():
            log.warning("Command connection closed by server")
            if self.on_event:
                self.on_event("DISCONNECTED")
