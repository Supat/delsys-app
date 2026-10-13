"""Main window of the Trigno desktop app (Windows 7+, also runs on macOS/Linux)."""

from __future__ import annotations

import html
import os
import subprocess
import sys
import time
from pathlib import Path

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .. import protocol
from ..plot import PlotPanel
from .controller import ARMED, CONNECTED, CONNECTING, DISCONNECTED, STREAMING, Controller, lsl_available, recordings_dir
from .dialogs import LSLSettingsDialog, SensorDialog, SystemSettingsDialog

PHASE_TEXT = {
    DISCONNECTED: ("Offline", "#888888"),
    CONNECTING: ("Connecting…", "#E8A33C"),
    CONNECTED: ("Ready", "#3CB371"),
    ARMED: ("Waiting for trigger", "#E8A33C"),
    STREAMING: ("Streaming", "#E0453A"),
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, controller: Controller):
        super().__init__()
        self.c = controller
        self.panel: PlotPanel | None = None
        self.setWindowTitle("Trigno")
        self.resize(1280, 820)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_plot_area())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([370, 910])
        self.setCentralWidget(splitter)
        self._build_console()

        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status)

        c = controller
        c.phase_changed.connect(self._on_phase)
        c.system_changed.connect(self._on_system)
        c.message.connect(self._on_message)
        c.alert.connect(lambda text: QtWidgets.QMessageBox.warning(self, "Trigno", text))
        c.stats_changed.connect(self._on_stats)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh_plot)
        self._timer.start(33)
        for box in (self.save, self.trig_start):
            box.toggled.connect(lambda _: self._on_phase(self.c.phase))
        self._on_phase(c.phase)
        self._on_system(None)

    # --- layout ------------------------------------------------------------------------------

    def _build_sidebar(self) -> QtWidgets.QWidget:
        c = self.c
        side = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(side)

        # SDK server
        box = QtWidgets.QGroupBox("SDK server")
        form = QtWidgets.QVBoxLayout(box)
        self.host = QtWidgets.QLineEdit(c.host)
        self.host.setPlaceholderText("IP of PC running Trigno Control Utility")
        self.host.returnPressed.connect(self._toggle_connection)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)
        self.server_info = QtWidgets.QLabel()
        self.server_info.setWordWrap(True)
        self.server_info.setStyleSheet("color: gray;")
        hint = QtWidgets.QLabel("On the PC running the Trigno Control Utility, use 127.0.0.1.")
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        form.addWidget(QtWidgets.QLabel("Server IP"))
        form.addWidget(self.host)
        form.addWidget(hint)
        form.addWidget(self.connect_button)
        form.addWidget(self.server_info)
        v.addWidget(box)

        # Acquisition
        box = self.acq_box = QtWidgets.QGroupBox("Acquisition")
        grid = QtWidgets.QFormLayout(box)
        self.save = QtWidgets.QCheckBox("Save to file")
        self.save.setChecked(c.save_to_file)
        self.save.toggled.connect(lambda on: (setattr(c, "save_to_file", on), c.save_settings()))
        self.trig_start = QtWidgets.QCheckBox("Wait for start trigger")
        self.trig_start.toggled.connect(lambda on: setattr(c, "trigger_start", on))
        self.trig_stop = QtWidgets.QCheckBox("Stop on stop trigger")
        self.trig_stop.toggled.connect(lambda on: setattr(c, "trigger_stop", on))
        self.duration = QtWidgets.QDoubleSpinBox()
        self.duration.setRange(0, 24 * 3600)
        self.duration.setDecimals(1)
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText("until stopped")
        self.duration.valueChanged.connect(lambda v: setattr(c, "duration_limit", v))
        self.window_combo = QtWidgets.QComboBox()
        for s in (2, 5, 10, 20):
            self.window_combo.addItem(f"{s} s", float(s))
        self.window_combo.setCurrentIndex(max(0, self.window_combo.findData(float(c.plot_window))))
        self.window_combo.currentIndexChanged.connect(lambda _: c.set_plot_window(self.window_combo.currentData()))
        self.record_button = QtWidgets.QPushButton("Record")
        self.record_button.setMinimumHeight(34)
        self.record_button.clicked.connect(self._toggle_acquisition)
        self.elapsed = QtWidgets.QLabel("–")
        self.samples = QtWidgets.QLabel("–")
        grid.addRow(self.save)
        grid.addRow(self.trig_start)
        grid.addRow(self.trig_stop)
        grid.addRow("Duration", self.duration)
        grid.addRow("Plot window", self.window_combo)
        grid.addRow(self.record_button)
        grid.addRow("Elapsed", self.elapsed)
        grid.addRow("Samples", self.samples)
        v.addWidget(box)

        # LSL
        box = QtWidgets.QGroupBox("Lab Streaming Layer")
        grid = QtWidgets.QFormLayout(box)
        self.lsl = QtWidgets.QCheckBox("Stream to LSL")
        self.lsl.setChecked(c.lsl_enabled)
        self.lsl.toggled.connect(lambda on: (setattr(c, "lsl_enabled", on), c.save_settings()))
        if not lsl_available():
            self.lsl.setEnabled(False)
            self.lsl.setToolTip("Install pylsl to enable LSL streaming.")
        self.lsl_button = QtWidgets.QPushButton()
        self.lsl_button.clicked.connect(self._edit_lsl)
        self.lsl_consumers = QtWidgets.QLabel("–")
        grid.addRow(self.lsl)
        grid.addRow("Stream", self.lsl_button)
        grid.addRow("Consumers", self.lsl_consumers)
        v.addWidget(box)
        self._update_lsl_button()

        # Sensors
        box = self.sensor_box = QtWidgets.QGroupBox("Sensors")
        col = QtWidgets.QVBoxLayout(box)
        self.sensor_list = QtWidgets.QListWidget()
        self.sensor_list.setMinimumHeight(110)
        self.sensor_list.itemActivated.connect(self._open_sensor)
        row = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(c.refresh)
        self.system_button = QtWidgets.QPushButton("System settings…")
        self.system_button.clicked.connect(self._open_system)
        row.addWidget(self.refresh_button)
        row.addWidget(self.system_button)
        col.addWidget(self.sensor_list)
        col.addLayout(row)
        v.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        folder = QtWidgets.QPushButton("Open recordings folder")
        folder.clicked.connect(self._open_recordings)
        console = QtWidgets.QPushButton("Console")
        console.clicked.connect(lambda: self.console.setVisible(not self.console.isVisible()))
        row.addWidget(folder)
        row.addWidget(console)
        v.addLayout(row)
        v.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _build_plot_area(self) -> QtWidgets.QWidget:
        self.stack = QtWidgets.QStackedWidget()
        self.placeholder = QtWidgets.QLabel()
        self.placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: gray; font-size: 15px;")
        self.plots = pg.GraphicsLayoutWidget()
        self.plot_scroll = QtWidgets.QScrollArea()
        self.plot_scroll.setWidget(self.plots)
        self.plot_scroll.setWidgetResizable(True)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.plot_scroll)
        return self.stack

    def _build_console(self) -> None:
        self.console = QtWidgets.QDockWidget("Console", self)
        self.console.setObjectName("console")
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.log.setFont(font)
        row = QtWidgets.QHBoxLayout()
        self.command = QtWidgets.QLineEdit()
        self.command.setPlaceholderText("SDK command, e.g. SENSOR 1 MODE?")
        self.command.setFont(font)
        self.command.returnPressed.connect(self._send_command)
        send = QtWidgets.QPushButton("Send")
        send.clicked.connect(self._send_command)
        row.addWidget(self.command)
        row.addWidget(send)
        v.addWidget(self.log)
        v.addLayout(row)
        self.console.setWidget(w)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.console)
        self.console.hide()

    # --- actions -----------------------------------------------------------------------------

    def _toggle_connection(self) -> None:
        if self.c.phase == DISCONNECTED:
            self.c.connect_server(self.host.text())
        elif self.c.phase != CONNECTING:
            self.c.disconnect_server()

    def _toggle_acquisition(self) -> None:
        if self.c.is_acquiring:
            self.c.stop_acquisition()
        else:
            self.c.start_acquisition()

    def _edit_lsl(self) -> None:
        dialog = LSLSettingsDialog(self.c.lsl, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.c.lsl = dialog.configuration()
            self.c.save_settings()
            self._update_lsl_button()

    def _update_lsl_button(self) -> None:
        self.lsl_button.setText(f"{self.c.lsl.name}  …")

    def _open_sensor(self, item: QtWidgets.QListWidgetItem) -> None:
        sensor = item.data(QtCore.Qt.ItemDataRole.UserRole)
        SensorDialog(sensor, self.c, self).exec()

    def _open_system(self) -> None:
        if self.c.system is not None:
            SystemSettingsDialog(self.c.system, self.c, self).exec()

    def _open_recordings(self) -> None:
        path = recordings_dir()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 - opens Explorer
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _send_command(self) -> None:
        text = self.command.text().strip()
        self.command.clear()
        if text:
            self.c.send_raw(text)

    # --- updates -----------------------------------------------------------------------------

    def _on_phase(self, phase: str) -> None:
        c = self.c
        text, color = PHASE_TEXT[phase]
        if phase == STREAMING and c.current_recording is not None:
            text = "Recording"
        self.status.setText(f'<span style="color:{color}">●</span> {text}')
        self.host.setEnabled(phase == DISCONNECTED)
        self.connect_button.setText({DISCONNECTED: "Connect", CONNECTING: "Connecting…"}.get(phase, "Disconnect"))
        self.connect_button.setEnabled(phase != CONNECTING and not c.is_acquiring)
        acquiring = c.is_acquiring
        self.record_button.setEnabled(c.is_connected)
        if c.stopping:
            self.record_button.setText("Stopping…")
            self.record_button.setEnabled(False)
        elif acquiring:
            self.record_button.setText("Stop")
            self.record_button.setStyleSheet("background-color: #E0453A; color: white; font-weight: bold;")
        else:
            self.record_button.setText("Arm" if self.trig_start.isChecked() else ("Record" if self.save.isChecked() else "Start"))
            self.record_button.setStyleSheet("font-weight: bold;")
        for w in (self.save, self.trig_start, self.trig_stop, self.duration, self.window_combo, self.lsl,
                  self.lsl_button, self.refresh_button):
            w.setEnabled(not acquiring and (w is not self.lsl or lsl_available()))
        self.sensor_box.setEnabled(c.is_connected)
        if not acquiring:
            self.lsl_consumers.setText("–")
        if phase == DISCONNECTED:
            self.c.system = None
            self._on_system(None)

    def _on_system(self, system) -> None:
        self.sensor_list.clear()
        if system is None:
            self.server_info.setText("")
            self.placeholder.setText("Not connected.\n\nEnter the IP of the PC running the Trigno Control Utility\n"
                                     "and press Connect.")
            self.stack.setCurrentWidget(self.placeholder)
            self.panel = None
            return
        self.server_info.setText(f"{system.base_serial} · EMG {system.emg_rate:.1f} Hz · AUX {system.aux_rate:.1f} Hz")
        for s in system.sensors:
            item = QtWidgets.QListWidgetItem(
                f"Sensor {s.slot}   {s.mode} · EMG ×{s.emg_channel_count} · AUX ×{s.aux_channel_count}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, s)
            self.sensor_list.addItem(item)
        if not system.sensors:
            self.placeholder.setText("No active sensors.\n\nPair sensors in the Trigno Control Utility, "
                                     "turn them on and press Refresh.")
            self.stack.setCurrentWidget(self.placeholder)
            self.panel = None
            return
        self.panel = PlotPanel(self.plots, self.c.buffers)
        self.plots.setMinimumHeight(max(300, 170 * self.panel.plot_count))
        self.stack.setCurrentWidget(self.plot_scroll)

    def _on_stats(self) -> None:
        c = self.c
        counts = c.sample_counts
        if counts:
            self.samples.setText(f"EMG {counts.get(protocol.EMG_PORT, 0):,} · AUX {counts.get(protocol.AUX_PORT, 0):,}")
        if c.is_acquiring:
            if c.data_started_at is not None:
                secs = int(time.monotonic() - c.data_started_at)
                self.elapsed.setText(f"{secs // 60}:{secs % 60:02d}")
            else:
                self.elapsed.setText("waiting for data…")
            if c.streamer is not None:
                self.lsl_consumers.setText("● Connected" if c.lsl_has_consumers else "○ None yet")
                self.lsl_consumers.setStyleSheet("color: #3CB371;" if c.lsl_has_consumers else "color: gray;")

    def _on_message(self, text: str, is_error: bool) -> None:
        stamp = time.strftime("%H:%M:%S")
        color = "#E0453A" if is_error else "palette(text)"
        self.log.appendHtml(f'<span style="color:gray">{stamp}</span> '
                            f'<span style="color:{color}">{html.escape(text)}</span>')
        self.statusBar().showMessage(text, 5000)

    def _refresh_plot(self) -> None:
        if self.panel is not None and self.c.is_acquiring:
            self.panel.refresh()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._timer.stop()
        try:
            self.c.shutdown()
        finally:
            event.accept()


def _icon() -> QtGui.QIcon:
    candidates = [Path(__file__).with_name("trigno.ico")]
    if hasattr(sys, "_MEIPASS"):  # PyInstaller build: bundled under assets/
        candidates.append(Path(sys._MEIPASS) / "assets" / "trigno.ico")
    for path in candidates:
        if path.exists():
            return QtGui.QIcon(str(path))
    return QtGui.QIcon()
