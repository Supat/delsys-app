"""Application state and Trigno session handling for the desktop GUI.

Blocking network calls run on a single worker thread (so commands stay in order);
results come back to the Qt main thread through a queued signal.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from pyqtgraph.Qt import QtCore

from ..client import TrignoError
from ..lsl import LSLConfiguration
from ..plot import SignalBuffers
from ..recorder import CsvRecorder
from ..sensors import SystemInfo, query_system
from ..session import TrignoSession

log = logging.getLogger(__name__)

DISCONNECTED, CONNECTING, CONNECTED, ARMED, STREAMING = "disconnected", "connecting", "connected", "armed", "streaming"


def recordings_dir() -> Path:
    docs = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(docs or Path.home()) / "Trigno" / "Recordings"


def lsl_available() -> bool:
    try:
        import pylsl  # noqa: F401
    except Exception:
        return False
    return True


class _Invoker(QtCore.QObject):
    call = QtCore.pyqtSignal(object) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda fn: fn())


class Controller(QtCore.QObject):
    _Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal
    phase_changed = _Signal(str)
    system_changed = _Signal(object)  # SystemInfo or None
    message = _Signal(str, bool)  # text, is_error
    alert = _Signal(str)
    stats_changed = _Signal()
    recordings_changed = _Signal()

    def __init__(self, settings: QtCore.QSettings):
        super().__init__()
        self.settings = settings
        self.phase = DISCONNECTED
        self.system: SystemInfo | None = None
        self.session: TrignoSession | None = None
        self.buffers: SignalBuffers | None = None
        self.recorder: CsvRecorder | None = None
        self.current_recording: Path | None = None
        self.streamer = None
        self.data_started_at: float | None = None
        self.sample_counts: dict[int, int] = {}
        self.lsl_has_consumers = False
        self.stopping = False

        # Options (persisted)
        self.host = settings.value("host", "192.168.11.7")
        self.save_to_file = settings.value("saveToFile", True, type=bool)
        self.trigger_start = False
        self.trigger_stop = False
        self.duration_limit = 0.0
        self.plot_window = settings.value("plotWindow", 5.0, type=float)
        self.lsl_enabled = settings.value("lslEnabled", False, type=bool) and lsl_available()
        try:
            self.lsl = LSLConfiguration.from_dict(json.loads(settings.value("lslConfiguration", "{}")))
        except (ValueError, TypeError):
            self.lsl = LSLConfiguration()

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trigno-cmd")
        self._invoker = _Invoker()
        self._monitor = QtCore.QTimer(self)
        self._monitor.setInterval(100)
        self._monitor.timeout.connect(self._tick)

    # --- helpers -------------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self.phase in (CONNECTED, ARMED, STREAMING)

    @property
    def is_acquiring(self) -> bool:
        return self.phase in (ARMED, STREAMING)

    def save_settings(self) -> None:
        s = self.settings
        s.setValue("host", self.host)
        s.setValue("saveToFile", self.save_to_file)
        s.setValue("plotWindow", self.plot_window)
        s.setValue("lslEnabled", self.lsl_enabled)
        s.setValue("lslConfiguration", json.dumps(self.lsl.to_dict()))

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self.phase_changed.emit(phase)

    def _info(self, text: str) -> None:
        log.info(text)
        self.message.emit(text, False)

    def _fail(self, exc: BaseException | str) -> None:
        text = str(exc) if not isinstance(exc, OSError) else f"Network error: {exc}"
        log.warning(text)
        self.message.emit(text, True)
        self.alert.emit(text)

    def _run(self, work: Callable, done: Callable | None = None, failed: Callable | None = None) -> None:
        """Run `work()` on the worker thread; call `done(result)` or `failed(exc)` on the UI thread."""

        def task():
            try:
                result = work()
            except BaseException as exc:  # noqa: BLE001 - reported to the user
                self._invoker.call.emit(lambda: (failed or self._fail)(exc))
            else:
                if done is not None:
                    self._invoker.call.emit(lambda: done(result))

        self._executor.submit(task)

    # --- connection ------------------------------------------------------------------------

    def connect_server(self, host: str) -> None:
        if self.phase != DISCONNECTED:
            return
        self.host = host.strip()
        self.save_settings()
        self._set_phase(CONNECTING)
        session = TrignoSession(self.host)

        def failed(exc):
            self._set_phase(DISCONNECTED)
            self._fail(exc)

        def done(system):
            self.session = session
            self._apply_system(system)
            self._set_phase(CONNECTED)
            self._info(f"Connected: {session.client.version}")
            self._monitor.start()

        def work():
            try:
                return session.open()
            except BaseException:
                session.close()
                raise

        self._run(work, done, failed)

    def disconnect_server(self) -> None:
        session = self.session
        if session is None:
            return
        if self.is_acquiring:
            self.stop_acquisition(then=self.disconnect_server)
            return
        self._monitor.stop()
        self.session = None

        def done(_):
            self._set_phase(DISCONNECTED)
            self._info("Disconnected")

        self._run(session.close, done)

    def refresh(self) -> None:
        session = self.session
        if session is None or self.is_acquiring:
            return
        self._run(lambda: query_system(session.client, session.start_index_base), self._apply_system)

    def _apply_system(self, system: SystemInfo) -> None:
        if self.session is not None:
            self.session.system = system
        self.system = system
        self.buffers = SignalBuffers(system, self.plot_window)
        self.system_changed.emit(system)

    def set_plot_window(self, seconds: float) -> None:
        self.plot_window = seconds
        self.save_settings()
        if self.system is not None and not self.is_acquiring:
            self._apply_system(self.system)

    # --- acquisition -----------------------------------------------------------------------

    def start_acquisition(self) -> None:
        session, system = self.session, self.system
        if session is None or system is None or self.phase != CONNECTED:
            return
        if not system.sensors:
            self.alert.emit("No active sensors. Pair them and turn them on, then refresh.")
            return
        trigger_start, trigger_stop = self.trigger_start, self.trigger_stop
        save, lsl_on, lsl_config = self.save_to_file, self.lsl_enabled, self.lsl
        self.buffers = SignalBuffers(system, self.plot_window)
        self.system_changed.emit(system)
        buffers = self.buffers

        def work():
            # A previous client (e.g. this app killed while recording) may have left the
            # base streaming, which makes the server reject configuration commands.
            try:
                session.client.send("STOP", timeout=20)
            except TrignoError:
                pass
            session.arm_triggers(start=trigger_start, stop=trigger_stop)
            recorder = streamer = None
            if save:
                out = recordings_dir() / datetime.now().strftime("%Y%m%d-%H%M%S")
                recorder = CsvRecorder(out, system)
            if lsl_on:
                from ..lsl import LSLStreamer

                streamer = LSLStreamer(system, lsl_config)
            session.sinks = [buffers.sink] + ([recorder.write] if recorder else []) + ([streamer.push] if streamer else [])
            try:
                session.start(wait_for_trigger=trigger_start)
            except BaseException:
                session.sinks = []
                if recorder is not None:
                    recorder.close()
                    _remove_tree(recorder.out_dir)
                if streamer is not None:
                    streamer.close()
                raise
            return recorder, streamer

        def done(result):
            self.recorder, self.streamer = result
            self.current_recording = self.recorder.out_dir if self.recorder else None
            self.data_started_at = None
            self.sample_counts = {}
            self._set_phase(ARMED if trigger_start else STREAMING)
            self._info("Armed, waiting for start trigger" if trigger_start else "Streaming")
            if self.streamer is not None:
                extra = f' + "{lsl_config.name} AUX"' if self.streamer.aux_labels else ""
                self._info(f'LSL: streaming "{lsl_config.name}" ({len(self.streamer.emg_labels)} ch){extra}')
            self.recordings_changed.emit()

        self._run(work, done)

    def stop_acquisition(self, by_trigger: bool = False, then: Callable | None = None) -> None:
        session = self.session
        if session is None or not self.is_acquiring or self.stopping:
            return
        recorder, streamer = self.recorder, self.streamer
        disarm = self.trigger_start or self.trigger_stop
        self.stopping = True
        self.phase_changed.emit(self.phase)  # let the UI show "Stopping…"

        def work():
            session.stop()
            counts = session.samples_received()
            session.sinks = []
            if disarm:
                # Leave the base station disarmed for whoever uses the TCU next.
                try:
                    session.arm_triggers(start=False, stop=False)
                except TrignoError:
                    pass
            saved = None
            if recorder is not None:
                saved = recorder.close().parent
            if streamer is not None:
                streamer.close()
            return counts, saved

        def failed(exc):
            self.stopping = False
            self._set_phase(CONNECTED)
            self._fail(exc)

        def done(result):
            counts, saved = result
            self.stopping = False
            self._set_phase(CONNECTED)
            self.sample_counts = counts
            self.recorder = self.streamer = None
            self.current_recording = None
            self.lsl_has_consumers = False
            self._info("Stopped by trigger" if by_trigger else "Stopped")
            if saved is not None:
                self._info(f"Saved {saved}")
            self.recordings_changed.emit()
            self.stats_changed.emit()
            if then is not None:
                then()

        self._run(work, done, failed)

    def _tick(self) -> None:
        session = self.session
        if session is None:
            return
        if session.disconnected.is_set():
            self._handle_disconnect()
            return
        if not self.is_acquiring or self.stopping:
            return
        if self.data_started_at is None and session.has_data():
            self.data_started_at = time.monotonic()
            if self.phase == ARMED:
                self._set_phase(STREAMING)
                self._info("Triggered")
        self.sample_counts = session.samples_received()
        if self.streamer is not None:
            self.lsl_has_consumers = self.streamer.has_consumers
        self.stats_changed.emit()
        if session.stopped_by_trigger.is_set():
            self.stop_acquisition(by_trigger=True)
        elif (self.data_started_at is not None and self.duration_limit > 0
              and time.monotonic() - self.data_started_at >= self.duration_limit):
            self.stop_acquisition()

    def _handle_disconnect(self) -> None:
        self._monitor.stop()
        session, recorder, streamer = self.session, self.recorder, self.streamer
        self.session = None
        self.recorder = self.streamer = None
        self.current_recording = None

        def work():
            for s in session.streams:
                s.stop()
            session.streams.clear()
            if recorder is not None:
                recorder.close()
            if streamer is not None:
                streamer.close()
            session.close()

        self._run(work, lambda _: self.recordings_changed.emit())
        self._set_phase(DISCONNECTED)
        self._fail("Lost connection to the Trigno SDK server.")

    # --- configuration -----------------------------------------------------------------------

    def _configure(self, commands: list[str]) -> None:
        session = self.session
        if session is None or self.is_acquiring:
            return

        def work():
            replies = [session.client.command(c) for c in commands]
            return replies, query_system(session.client, session.start_index_base)

        def done(result):
            replies, system = result
            for c, r in zip(commands, replies):
                self._info(f"{c} -> {r}")
            self._apply_system(system)

        self._run(work, done)

    def configure_sensor(self, slot: int, mode: int | None, range_: str | None, bandwidth: str | None) -> None:
        commands = []
        if mode is not None:
            commands.append(f"SENSOR {slot} SETMODE {mode}")
        if range_:
            commands.append(f"SENSOR {slot} SETRANGE {range_}")
        if bandwidth:
            commands.append(f"SENSOR {slot} SETBANDWIDTH {bandwidth}")
        if commands:
            self._configure(commands)

    def set_backwards_compatibility(self, on: bool) -> None:
        self._configure([f"BACKWARDS COMPATIBILITY {'ON' if on else 'OFF'}"])

    def set_upsampling(self, on: bool) -> None:
        self._configure([f"UPSAMPLE {'ON' if on else 'OFF'}"])

    def pair(self, slot: int) -> None:
        session = self.session
        if session is None or self.is_acquiring:
            return

        def done(reply):
            self._info(reply)
            self.alert.emit(f"Turn the sensor on and press and hold its button to pair it to slot {slot}. "
                            "Then press Refresh.")

        self._run(lambda: session.client.command(f"SENSOR {slot} PAIR"), done)

    def send_raw(self, command: str) -> None:
        session = self.session
        if session is None or not command.strip():
            return
        self._info(f"> {command}")
        self._run(lambda: session.client.send(command), lambda reply: self._info(reply))

    def shutdown(self) -> None:
        """Called on window close: stop acquisition and disconnect, synchronously."""
        self._monitor.stop()
        self.save_settings()
        session = self.session
        self.session = None
        if session is not None:
            def work():
                if self.is_acquiring:
                    session.stop()
                    try:
                        session.arm_triggers(start=False, stop=False)
                    except TrignoError:
                        pass
                if self.recorder is not None:
                    self.recorder.close()
                if self.streamer is not None:
                    self.streamer.close()
                session.close()

            self._executor.submit(work).result(timeout=30)
        self._executor.shutdown(wait=False)


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


__all__ = ["Controller", "recordings_dir", "lsl_available"]
