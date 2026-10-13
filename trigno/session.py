"""High-level acquisition session: configure, start (command or trigger), stream, stop."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from . import protocol
from .client import CommandClient
from .sensors import SystemInfo, query_system
from .stream import DataStream

log = logging.getLogger(__name__)

Sink = Callable[[int, np.ndarray, int, float], None]


class TrignoSession:
    def __init__(self, host: str = "localhost", start_index_base: int | None = None):
        self.host = host
        self.start_index_base = start_index_base
        self.client = CommandClient(host, on_event=self._on_event)
        self.system: SystemInfo | None = None
        self.streams: list[DataStream] = []
        self.sinks: list[Sink] = []
        self.stopped_by_trigger = threading.Event()
        self.disconnected = threading.Event()
        self.running = False

    def __enter__(self) -> TrignoSession:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> SystemInfo:
        self.client.connect()
        self.system = query_system(self.client, self.start_index_base)
        return self.system

    def close(self) -> None:
        if self.running:
            self.stop()
        for s in self.streams:
            s.stop()
        self.streams.clear()
        self.client.close()

    def add_sink(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def arm_triggers(self, start: bool, stop: bool) -> None:
        self.client.command(f"TRIGGER START {'ON' if start else 'OFF'}")
        self.client.command(f"TRIGGER STOP {'ON' if stop else 'OFF'}")

    def start(self, wait_for_trigger: bool = False) -> None:
        """Open the data ports and begin acquisition.

        With wait_for_trigger, START is not sent: the base station begins
        streaming when it receives the hardware start trigger (section 4).
        """
        endian = "big" if "BIG" in (self.system.endianness or "").upper() else "little"
        for port in (protocol.EMG_PORT, protocol.AUX_PORT):
            stream = DataStream(self.host, port, self._dispatch, endian=endian)
            stream.connect()
            self.streams.append(stream)
        for s in self.streams:
            s.start()
        if not wait_for_trigger:
            self.client.command("START", timeout=20)
        self.running = True

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if not self.stopped_by_trigger.is_set() and not self.disconnected.is_set():
            try:
                self.client.command("STOP", timeout=20)
            except Exception:
                log.exception("STOP failed")
        # Give the reader threads a moment to flush the last frames.
        time.sleep(0.2)

    def wait_for_data(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not any(s.first_data_time for s in self.streams):
            if self.stopped_by_trigger.is_set() or self.disconnected.is_set():
                return False
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True

    def _dispatch(self, port: int, block: np.ndarray, start: int, host_time: float) -> None:
        for sink in self.sinks:
            sink(port, block, start, host_time)

    def _on_event(self, message: str) -> None:
        if message.startswith("STOPPED"):
            self.stopped_by_trigger.set()
        elif message == "DISCONNECTED":
            self.disconnected.set()
