"""Live scrolling plot of active channels (pyqtgraph)."""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from . import protocol
from .sensors import SystemInfo


class RingBuffer:
    def __init__(self, length: int, columns: list[int]):
        self.columns = columns
        self.data = np.zeros((length, len(columns)), dtype=np.float32)
        self.lock = threading.Lock()

    def push(self, block: np.ndarray) -> None:
        block = block[:, self.columns]
        n = min(len(block), len(self.data))
        with self.lock:
            self.data = np.roll(self.data, -n, axis=0)
            self.data[-n:] = block[-n:]

    def snapshot(self) -> np.ndarray:
        with self.lock:
            return self.data.copy()


class LivePlot:
    def __init__(self, system: SystemInfo, window_s: float = 5.0):
        self.system = system
        self.window_s = window_s
        self.buffers: dict[int, RingBuffer] = {}
        self._layout: list[tuple] = []  # (port, buffer column, curve, rate)

        for port, name, rate in (
            (protocol.EMG_PORT, "emg", system.emg_rate),
            (protocol.AUX_PORT, "aux", system.aux_rate),
        ):
            cols = [c.column for s in system.sensors for c in s.channels if c.port == name]
            if cols:
                self.buffers[port] = RingBuffer(max(1, int(window_s * rate)), cols)

    def sink(self, port: int, block: np.ndarray, start: int, host_time: float) -> None:
        buf = self.buffers.get(port)
        if buf is not None:
            buf.push(block)

    def run(self, poll: Callable[[], bool] | None = None, title: str = "Trigno live") -> None:
        """Show the window and block until it is closed or `poll()` returns False."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore

        app = pg.mkQApp(title)
        win = self.window = pg.GraphicsLayoutWidget(title=title)
        win.resize(1200, max(300, 180 * len(self.system.sensors)))
        palette = ["#4C9BE8", "#E8744C", "#5CC27A", "#C25CC2", "#E8C14C", "#4CC2C2", "#A0A0A0", "#E84C8A", "#8A8AE8"]

        for row, sensor in enumerate(self.system.sensors):
            for col, (port, name) in enumerate(((protocol.EMG_PORT, "emg"), (protocol.AUX_PORT, "aux"))):
                chans = [c for c in sensor.channels if c.port == name]
                if not chans:
                    continue
                p = win.addPlot(row=row, col=col, title=f"Sensor {sensor.slot} {name.upper()}")
                p.addLegend(offset=(5, 5))
                p.showGrid(x=True, y=True, alpha=0.2)
                rate = self.system.emg_rate if port == protocol.EMG_PORT else self.system.aux_rate
                for i, ch in enumerate(chans):
                    curve = p.plot(pen=pg.mkPen(palette[i % len(palette)], width=1), name=f"ch{ch.number} {ch.units}")
                    buf_col = self.buffers[port].columns.index(ch.column)
                    self._layout.append((port, buf_col, curve, rate))

        def refresh():
            snaps = {port: buf.snapshot() for port, buf in self.buffers.items()}
            for port, buf_col, curve, rate in self._layout:
                y = snaps[port][:, buf_col]
                curve.setData(np.arange(len(y)) / rate - len(y) / rate, y)
            if poll is not None and not poll():
                win.close()

        timer = QtCore.QTimer()
        timer.timeout.connect(refresh)
        timer.start(33)
        win.show()
        app.exec()
        timer.stop()
