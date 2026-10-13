"""Dialogs: sensor details/configuration, system settings, LSL settings."""

from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from ..lsl import ACTIVE_CHANNELS, DAQ_COMPATIBLE, LSLConfiguration, library_version
from ..sensors import SensorInfo, SystemInfo


def _footer(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


class SensorDialog(QtWidgets.QDialog):
    def __init__(self, sensor: SensorInfo, controller, parent=None):
        super().__init__(parent)
        self.sensor = sensor
        self.controller = controller
        self.setWindowTitle(f"Sensor {sensor.slot}")
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QFormLayout()
        info.addRow("Type", QtWidgets.QLabel(sensor.type))
        info.addRow("Serial", QtWidgets.QLabel(sensor.serial))
        info.addRow("Firmware", QtWidgets.QLabel(sensor.firmware))
        info.addRow("Mode", QtWidgets.QLabel(sensor.mode))
        layout.addLayout(info)

        table = QtWidgets.QTableWidget(len(sensor.channels), 5)
        table.setHorizontalHeaderLabels(["Channel", "Port", "Column", "Rate (Hz)", "Units"])
        for row, c in enumerate(sensor.channels):
            values = [f"ch{c.number}", c.port.upper(), str(c.column),
                      f"{c.rate:.2f}" if c.rate else "?", c.units]
            for col, v in enumerate(values):
                item = QtWidgets.QTableWidgetItem(v)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, item)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.resizeColumnsToContents()
        layout.addWidget(table)

        box = QtWidgets.QGroupBox("Configure")
        form = QtWidgets.QFormLayout(box)
        self.mode = QtWidgets.QLineEdit()
        self.mode.setPlaceholderText("unchanged (e.g. 9)")
        self.range = QtWidgets.QComboBox()
        self.range.addItem("Unchanged", "")
        self.range.addItem("Low ±5.5 mV", "LOW")
        self.range.addItem("High ±11 mV", "HIGH")
        self.bandwidth = QtWidgets.QComboBox()
        self.bandwidth.addItem("Unchanged", "")
        self.bandwidth.addItem("Narrow 20–450 Hz", "NARROW")
        self.bandwidth.addItem("Wide 10–850 Hz", "WIDE")
        form.addRow("New mode", self.mode)
        form.addRow("EMG range", self.range)
        form.addRow("EMG bandwidth", self.bandwidth)
        form.addRow(_footer("Mode numbers are listed in section 7 of the Trigno SDK User Guide. "
                            "Range and bandwidth apply to Avanti sensors. Settings can't be changed while streaming."))
        box.setEnabled(not controller.is_acquiring)
        layout.addWidget(box)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Apply | QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setEnabled(not controller.is_acquiring)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(520, 480)

    def apply(self) -> None:
        text = self.mode.text().strip()
        if text and not text.isdigit():
            QtWidgets.QMessageBox.warning(self, "Trigno", "Mode must be a number.")
            return
        self.controller.configure_sensor(self.sensor.slot, int(text) if text else None,
                                         self.range.currentData() or None, self.bandwidth.currentData() or None)
        self.accept()


class SystemSettingsDialog(QtWidgets.QDialog):
    def __init__(self, system: SystemInfo, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("System settings")
        layout = QtWidgets.QVBoxLayout(self)

        base = QtWidgets.QGroupBox("Base station")
        form = QtWidgets.QFormLayout(base)
        form.addRow("Server", QtWidgets.QLabel(system.version))
        form.addRow("Serial", QtWidgets.QLabel(system.base_serial))
        form.addRow("Firmware", QtWidgets.QLabel(system.base_firmware))
        form.addRow("Frame interval", QtWidgets.QLabel(f"{system.frame_interval * 1000:.1f} ms"))
        form.addRow("Triggers", QtWidgets.QLabel(system.trigger))
        layout.addWidget(base)

        sampling = QtWidgets.QGroupBox("Sampling")
        form = QtWidgets.QFormLayout(sampling)
        self.compat = QtWidgets.QCheckBox("Backwards compatibility")
        self.compat.setChecked("YES" in system.backwards_compatibility.upper())
        self.compat.toggled.connect(controller.set_backwards_compatibility)
        self.upsample = QtWidgets.QCheckBox("Upsample EMG to 2000 Hz")
        self.upsample.setChecked("ON" in system.upsampling.upper())
        self.upsample.toggled.connect(controller.set_upsampling)
        form.addRow(self.compat)
        form.addRow(self.upsample)
        form.addRow("EMG port rate", QtWidgets.QLabel(f"{system.emg_rate:.2f} Hz"))
        form.addRow("AUX port rate", QtWidgets.QLabel(f"{system.aux_rate:.2f} Hz"))
        form.addRow(_footer("With backwards compatibility on, EMG is locked to 2000 Hz (upsampled) or 1925.9 Hz "
                            "and AUX to 148.1 Hz. Turn it off to stream at the sensors' native rates."))
        layout.addWidget(sampling)

        pair = QtWidgets.QGroupBox("Pair sensor")
        row = QtWidgets.QHBoxLayout(pair)
        self.slot = QtWidgets.QSpinBox()
        self.slot.setRange(1, 16)
        self.slot.setPrefix("Slot ")
        start = QtWidgets.QPushButton("Start pairing")
        start.clicked.connect(lambda: controller.pair(self.slot.value()))
        row.addWidget(self.slot)
        row.addWidget(start)
        row.addStretch()
        layout.addWidget(pair)

        for box in (sampling, pair):
            box.setEnabled(not controller.is_acquiring)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(460, 0)


class LSLSettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: LSLConfiguration, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LSL settings")
        layout = QtWidgets.QVBoxLayout(self)

        stream = QtWidgets.QGroupBox("EMG stream")
        form = QtWidgets.QFormLayout(stream)
        self.name = QtWidgets.QLineEdit(config.name)
        self.type = QtWidgets.QLineEdit(config.type)
        self.source_id = QtWidgets.QLineEdit(config.source_id)
        form.addRow("Name", self.name)
        form.addRow("Type", self.type)
        form.addRow("Source ID", self.source_id)
        form.addRow(_footer("Defaults match the NI-DAQ app (Daq_revised.py), so LabRecorder and existing "
                            "scripts find the stream unchanged."))
        layout.addWidget(stream)

        channels = QtWidgets.QGroupBox("Channels")
        form = QtWidgets.QFormLayout(channels)
        self.layout_combo = QtWidgets.QComboBox()
        self.layout_combo.addItem("16 slots (DAQ layout)", DAQ_COMPATIBLE)
        self.layout_combo.addItem("Active sensors only", ACTIVE_CHANNELS)
        self.layout_combo.setCurrentIndex(0 if config.layout == DAQ_COMPATIBLE else 1)
        self.scale = QtWidgets.QDoubleSpinBox()
        self.scale.setDecimals(4)
        self.scale.setRange(1e-4, 1e6)
        self.scale.setValue(config.emg_scale)
        self.include_aux = QtWidgets.QCheckBox("Also stream AUX (<name> AUX)")
        self.include_aux.setChecked(config.include_aux)
        form.addRow("Channels", self.layout_combo)
        form.addRow("EMG scale", self.scale)
        form.addRow(self.include_aux)
        form.addRow(_footer("16 slots keeps channel n = sensor slot n, with zeros for empty slots. The SDK reports "
                            "volts at the electrode; the DAQ recorded the base's amplified analog output, so set "
                            "EMG scale to that gain if consumers expect the old magnitudes."))
        layout.addWidget(channels)

        try:
            version = library_version()
        except Exception:
            version = "not installed"
        layout.addWidget(_footer(f"liblsl {version}"))

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel | QtWidgets.QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._restore)
        layout.addWidget(buttons)
        self.resize(460, 0)

    def _restore(self) -> None:
        d = LSLConfiguration()
        self.name.setText(d.name)
        self.type.setText(d.type)
        self.source_id.setText(d.source_id)
        self.layout_combo.setCurrentIndex(0)
        self.scale.setValue(d.emg_scale)
        self.include_aux.setChecked(d.include_aux)

    def configuration(self) -> LSLConfiguration:
        return LSLConfiguration(
            name=self.name.text().strip() or "Delsys",
            type=self.type.text().strip() or "EMG Signal",
            source_id=self.source_id.text().strip() or "EMG_Signal",
            layout=self.layout_combo.currentData(),
            include_aux=self.include_aux.isChecked(),
            emg_scale=self.scale.value(),
        )
