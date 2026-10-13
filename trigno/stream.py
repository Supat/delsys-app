"""Readers for the multiplexed float32 data ports (section 6.1.1)."""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

import numpy as np

from . import protocol

log = logging.getLogger(__name__)

# Called with (port, samples[n, channels], index of first sample, host receive time).
FrameCallback = Callable[[int, np.ndarray, int, float], None]


class DataStream:
    """Connects to one data port and demultiplexes it into (samples, channels) blocks.

    Bytes are only consumed in whole multiples of channels * 4 so that the
    channel alignment is never lost, as the manual requires.
    """

    def __init__(
        self,
        host: str,
        port: int,
        callback: FrameCallback,
        endian: str = "little",
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.channels = protocol.PORT_CHANNELS[port]
        self.callback = callback
        self.dtype = np.dtype("<f4" if endian.lower().startswith("l") else ">f4")
        self.timeout = timeout
        self.samples_received = 0
        self.first_data_time: float | None = None
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # Data only flows after START (or a start trigger); poll so stop() stays responsive.
        self._sock.settimeout(1.0)

    def start(self) -> None:
        if self._sock is None:
            self.connect()
        self._thread = threading.Thread(target=self._run, name=f"trigno-{self.port}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            self._sock.close()
            self._sock = None

    def _run(self) -> None:
        frame_bytes = self.channels * protocol.BYTES_PER_SAMPLE
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                log.info("Port %d closed by server", self.port)
                break
            now = time.time()
            buf += chunk
            usable = len(buf) - len(buf) % frame_bytes
            if not usable:
                continue
            block = np.frombuffer(bytes(buf[:usable]), dtype=self.dtype).reshape(-1, self.channels)
            del buf[:usable]
            if self.first_data_time is None:
                self.first_data_time = now
            start = self.samples_received
            self.samples_received += block.shape[0]
            try:
                self.callback(self.port, block.astype(np.float32, copy=False), start, now)
            except Exception:
                log.exception("Data callback failed on port %d", self.port)
