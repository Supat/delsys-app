"""Live scrolling plot of active channels (pyqtgraph)."""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from . import protocol
from .sensors import SystemInfo

PALETTE = ["#4C9BE8", "#E8744C", "#5CC27A", "#C25CC2", "#E8C14C", "#4CC2C2", "#A0A0A0", "#E84C8A", "#8A8AE8"]


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


class SignalBuffers:
    """The last `window_s` seconds of every active channel, per port; thread-safe."""

    def __init__(self, system: SystemInfo, window_s: float = 5.0):
        self.system = system
        self.window_s = window_s
        self.buffers: dict[int, RingBuffer] = {}
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


class PlotPanel:
    """Builds per-sensor plots into a pyqtgraph GraphicsLayoutWidget and refreshes them.

    One row per EMG channel, plus one row per group of AUX channels sharing units
    (g, deg/s, …), the same layout as the iPad/Mac app.
    """

    def __init__(self, layout_widget, buffers: SignalBuffers):
        import pyqtgraph as pg

        self.widget = layout_widget
        self.buffers = buffers
        self._curves: list[tuple] = []  # (port, buffer column, curve, rate)
        system = buffers.system
        layout_widget.clear()

        row = 0
        for sensor in system.sensors:
            groups: list[tuple[str, str, list]] = []
            for ch in sensor.channels:
                if ch.port == "emg":
                    groups.append((f"Sensor {sensor.slot} · EMG ch{ch.number}", "emg", [ch]))
            aux_units: dict[str, list] = {}
            for ch in sensor.channels:
                if ch.port == "aux":
                    aux_units.setdefault(ch.units, []).append(ch)
            for units, chans in aux_units.items():
                groups.append((f"Sensor {sensor.slot} · AUX ({units})", "aux", chans))

            for title, name, chans in groups:
                port = protocol.EMG_PORT if name == "emg" else protocol.AUX_PORT
                rate = system.emg_rate if name == "emg" else system.aux_rate
                p = layout_widget.addPlot(row=row, col=0, title=title)
                p.showGrid(x=True, y=True, alpha=0.2)
                p.setXRange(-buffers.window_s, 0, padding=0)
                p.setMouseEnabled(x=False, y=True)
                units = chans[0].units
                left = p.getAxis("left")
                left.setWidth(70)
                # Volts get SI prefixes (µV, mV); other units (g, deg/s) are shown as-is.
                left.enableAutoSIPrefix(units == "V")
                p.setLabel("left", units=units)
                if len(chans) > 1:
                    p.addLegend(offset=(5, 2), colCount=len(chans))
                for i, ch in enumerate(chans):
                    curve = p.plot(pen=pg.mkPen(PALETTE[i % len(PALETTE)], width=1),
                                   name=f"ch{ch.number} {ch.units}")
                    curve.setDownsampling(auto=True, method="peak")
                    curve.setClipToView(True)
                    buf_col = buffers.buffers[port].columns.index(ch.column)
                    self._curves.append((port, buf_col, curve, rate))
                row += 1

    @property
    def plot_count(self) -> int:
        return len({id(c) for c in self.widget.ci.items})

    def refresh(self) -> None:
        snaps = {port: buf.snapshot() for port, buf in self.buffers.buffers.items()}
        for port, buf_col, curve, rate in self._curves:
            y = snaps[port][:, buf_col]
            curve.setData(np.arange(len(y)) / rate - len(y) / rate, y)


class LivePlot(SignalBuffers):
    """Standalone live-plot window used by `trigno record --plot`."""

    def run(self, poll: Callable[[], bool] | None = None, title: str = "Trigno live") -> None:
        """Show the window and block until it is closed or `poll()` returns False."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore

        app = pg.mkQApp(title)
        win = self.window = pg.GraphicsLayoutWidget(title=title)
        panel = PlotPanel(win, self)
        win.resize(1200, max(300, 150 * panel.plot_count))

        def refresh():
            panel.refresh()
            if poll is not None and not poll():
                win.close()

        timer = QtCore.QTimer()
        timer.timeout.connect(refresh)
        timer.start(33)
        win.show()
        app.exec_() if hasattr(app, "exec_") else app.exec()
        timer.stop()
