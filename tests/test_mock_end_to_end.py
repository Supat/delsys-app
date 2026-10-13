import csv
import json
import time

import numpy as np
import pytest

from trigno import CommandClient, TrignoSession, query_system
from trigno.mock_server import MockSensor, MockTrignoServer
from trigno.recorder import CsvRecorder
from trigno.sensors import detect_start_index_base

HOST = "127.0.0.1"


@pytest.fixture(params=[True, False], ids=["one-based", "zero-based"])
def server(request):
    srv = MockTrignoServer(
        HOST,
        [MockSensor(1), MockSensor(3, aux_units=("g", "g", "g"))],
        one_based_start_index=request.param,
        trigger_delay=0.3,
        trigger_duration=0.6,
    )
    srv.start()
    yield srv
    srv.shutdown()


def test_detect_start_index_base():
    assert detect_start_index_base({1: 0, 2: 1}) == 0
    assert detect_start_index_base({1: 1, 2: 2}) == 1
    assert detect_start_index_base({3: 3}) == 1
    assert detect_start_index_base({3: 2}) == 0
    assert detect_start_index_base({}) == 0


def test_discovery_maps_columns(server):
    with CommandClient(HOST) as client:
        info = query_system(client)
    assert info.emg_rate == pytest.approx(2000)
    assert info.aux_rate == pytest.approx(148.148, rel=1e-3)
    by_slot = {s.slot: s for s in info.sensors}
    assert set(by_slot) == {1, 3}
    s3 = by_slot[3]
    assert s3.start_index == 2
    emg = [c for c in s3.channels if c.port == "emg"]
    aux = [c for c in s3.channels if c.port == "aux"]
    assert [c.column for c in emg] == [2]
    assert [c.column for c in aux] == [18, 19, 20]


def test_record_to_csv(server, tmp_path):
    with TrignoSession(HOST) as session:
        rec = CsvRecorder(tmp_path, session.system)
        session.add_sink(rec.write)
        session.arm_triggers(False, False)
        session.start()
        assert session.wait_for_data(timeout=2)
        time.sleep(1.0)
        session.stop()
        meta = json.loads(rec.close().read_text())

    with open(tmp_path / "emg.csv") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], np.array(rows[1:], dtype=float)
    assert header == ["time_s", "S1_emg1_V", "S3_emg1_V"]
    assert 1500 < len(data) < 3000
    assert len(data) == meta["samples"]["emg"]
    np.testing.assert_allclose(np.diff(data[:, 0]), 1 / 2000, rtol=1e-6)
    # Mock EMG for slot n is a 50*n Hz sine: check each column carries its own sensor.
    for col, freq in ((1, 50), (2, 150)):
        spectrum = np.abs(np.fft.rfft(data[:, col]))
        peak = np.fft.rfftfreq(len(data), 1 / 2000)[spectrum.argmax()]
        assert peak == pytest.approx(freq, abs=3)

    with open(tmp_path / "aux.csv") as f:
        rows = list(csv.reader(f))
    aux = np.array(rows[1:], dtype=float)
    assert len(rows[0]) == 1 + 6 + 3
    # Mock AUX values oscillate around the slot number.
    assert np.mean(aux[:, 1]) == pytest.approx(1, abs=0.6)
    assert np.mean(aux[:, 7]) == pytest.approx(3, abs=0.6)


def test_hardware_triggers(server):
    got = []
    with TrignoSession(HOST) as session:
        session.add_sink(lambda port, block, start, t: got.append((port, len(block))))
        session.arm_triggers(start=True, stop=True)
        session.start(wait_for_trigger=True)
        assert session.wait_for_data(timeout=3)
        assert session.stopped_by_trigger.wait(timeout=3)
        session.stop()
        session.arm_triggers(False, False)
    emg_samples = sum(n for port, n in got if port == 50043)
    assert 0.6 * 2000 * 0.7 < emg_samples < 0.6 * 2000 * 1.5


def test_invalid_command_reply(server):
    with CommandClient(HOST) as client:
        assert client.send("NOT A COMMAND") == "INVALID COMMAND"
        assert client.send("SENSOR 2 PAIRED?") == "NO"
