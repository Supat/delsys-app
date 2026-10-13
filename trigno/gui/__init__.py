"""Trigno desktop app: `trigno-gui` (or `python -m trigno.gui`)."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trigno-gui", description="Trigno desktop app")
    p.add_argument("--host", help="SDK server IP (overrides the saved one)")
    p.add_argument("--connect", action="store_true", help="connect on launch")
    p.add_argument("--record", action="store_true", help="start acquisition after connecting")
    p.add_argument("--duration", type=float, default=0, help="seconds to record (0 = until stopped)")
    p.add_argument("--lsl", action="store_true", help="enable LSL streaming")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from pyqtgraph.Qt import QtCore, QtWidgets

    if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):  # Qt 5 only; Qt 6 always scales
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("Trigno")
    app.setOrganizationName("Trigno")

    from .controller import Controller, lsl_available
    from .window import MainWindow, _icon

    app.setWindowIcon(_icon())
    controller = Controller(QtCore.QSettings())
    if args.host:
        controller.host = args.host
    if args.lsl and lsl_available():
        controller.lsl_enabled = True
    controller.duration_limit = args.duration
    window = MainWindow(controller)
    window.host.setText(controller.host)
    window.lsl.setChecked(controller.lsl_enabled)
    window.duration.setValue(args.duration)
    window.show()

    if args.connect:
        if args.record:
            def start_when_connected(phase):
                if phase == "connected" and controller.sample_counts == {} and not controller.is_acquiring:
                    controller.phase_changed.disconnect(start_when_connected)
                    controller.start_acquisition()

            controller.phase_changed.connect(start_when_connected)
        controller.connect_server(controller.host)

    return app.exec_() if hasattr(app, "exec_") else app.exec()
