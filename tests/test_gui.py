"""Desktop GUI end-to-end tests against the mock server (offscreen Qt)."""

import csv
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("pyqtgraph.Qt").QtCore
QtWidgets = pytest.importorskip("pyqtgraph.Qt").QtWidgets

from trigno.gui import controller as controller_mod  # noqa: E402
from trigno.gui.controller import CONNECTED, DISCONNECTED, STREAMING, Controller  # noqa: E402
from trigno.gui.window import MainWindow  # noqa: E402
from trigno.mock_server import MockSensor, MockTrignoServer  # noqa: E402

HOST = "127.0.0.1"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def server():
    srv = MockTrignoServer(HOST, [MockSensor(1), MockSensor(2, aux_units=("g", "g", "g"))],
                           trigger_delay=0.5, trigger_duration=1.0)
    srv.start()
    yield srv
    srv.shutdown()


@pytest.fixture
def gui(app, tmp_path, monkeypatch):
    monkeypatch.setattr(controller_mod, "recordings_dir", lambda: tmp_path / "Recordings")
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    c = Controller(settings)
    w = MainWindow(c)
    yield c, w
    w.close()


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def connect(c, w):
    w.host.setText(HOST)
    w.connect_button.click()
    assert wait_until(lambda: c.phase == CONNECTED), c.phase
    assert [s.slot for s in c.system.sensors] == [1, 2]
    assert w.sensor_list.count() == 2


def test_connect_record_stop(server, gui, tmp_path):
    c, w = gui
    connect(c, w)
    w.record_button.click()
    assert wait_until(lambda: c.phase == STREAMING)
    assert w.record_button.text() == "Stop"
    assert wait_until(lambda: c.sample_counts.get(50043, 0) > 2000)
    w.record_button.click()
    assert wait_until(lambda: c.phase == CONNECTED and c.recorder is None)

    [rec] = list((tmp_path / "Recordings").iterdir())
    with open(rec / "emg.csv") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["time_s", "S1_emg1_V", "S2_emg1_V"]
    assert len(rows) - 1 == c.sample_counts[50043]
    assert (rec / "metadata.json").exists()

    # A second run on the same connection must work (data ports released on stop).
    w.record_button.click()
    assert wait_until(lambda: c.phase == STREAMING)
    assert wait_until(lambda: c.sample_counts.get(50043, 0) > 1000)
    w.record_button.click()
    assert wait_until(lambda: c.phase == CONNECTED and c.recorder is None)
    assert len(list((tmp_path / "Recordings").iterdir())) == 2

    w.connect_button.click()
    assert wait_until(lambda: c.phase == DISCONNECTED)


def test_trigger_start_stop_and_duration(server, gui):
    c, w = gui
    connect(c, w)
    w.trig_start.setChecked(True)
    w.trig_stop.setChecked(True)
    w.save.setChecked(False)
    w.record_button.click()
    assert wait_until(lambda: c.phase == "armed")
    assert wait_until(lambda: c.phase == STREAMING, timeout=5)
    # The mock fires the stop trigger 1 s after the start trigger.
    assert wait_until(lambda: c.phase == CONNECTED and c.streamer is None and c.recorder is None, timeout=5)
    assert 1000 < c.sample_counts[50043] < 3500
    assert "START OFF STOP OFF" in server.handle("TRIGGER?")

    w.trig_start.setChecked(False)
    w.trig_stop.setChecked(False)
    w.duration.setValue(1.0)
    w.record_button.click()
    assert wait_until(lambda: c.phase == STREAMING)
    assert wait_until(lambda: c.phase == CONNECTED, timeout=5)
    assert 1800 < c.sample_counts[50043] < 3200


def test_lsl_stream(server, gui):
    pylsl = pytest.importorskip("pylsl")
    c, w = gui
    connect(c, w)
    c.lsl.name = c.lsl.source_id = f"TrignoGuiTest-{os.getpid()}"
    w.lsl.setChecked(True)
    w.save.setChecked(False)
    w.record_button.click()
    assert wait_until(lambda: c.phase == STREAMING)

    streams = pylsl.resolve_byprop("source_id", c.lsl.source_id, minimum=1, timeout=5)
    assert streams, "LSL stream not found"
    inlet = pylsl.StreamInlet(streams[0])
    assert inlet.info().channel_count() == 16
    inlet.open_stream(timeout=5)
    received = []
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        chunk, _ = inlet.pull_chunk(timeout=0.1)
        received += chunk
    assert len(received) > 1000
    assert all(row[2] == 0 for row in received)  # slot 3 is empty in the DAQ layout
    assert wait_until(lambda: c.lsl_has_consumers)
    w.record_button.click()
    assert wait_until(lambda: c.phase == CONNECTED and c.streamer is None)
