"""Writes streamed data to CSV (one file per port) plus a JSON metadata sidecar."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np

from . import protocol
from .sensors import SystemInfo


class CsvRecorder:
    def __init__(self, out_dir: str | Path, system: SystemInfo):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.system = system
        self.rates = {protocol.EMG_PORT: system.emg_rate, protocol.AUX_PORT: system.aux_rate}
        self._lock = threading.Lock()
        self._files = {}
        self._columns: dict[int, list[int]] = {}
        self._counts = {protocol.EMG_PORT: 0, protocol.AUX_PORT: 0}
        self._first_host_time: dict[int, float] = {}
        self.started_at = time.time()

        for port, name in ((protocol.EMG_PORT, "emg"), (protocol.AUX_PORT, "aux")):
            channels = [c for s in system.sensors for c in s.channels if c.port == name]
            if not channels:
                continue
            self._columns[port] = [c.column for c in channels]
            f = open(self.out_dir / f"{name}.csv", "w", newline="")
            f.write(",".join(["time_s"] + [c.label for c in channels]) + "\n")
            self._files[port] = f

    def write(self, port: int, block: np.ndarray, start: int, host_time: float) -> None:
        if port not in self._files:
            return
        n = block.shape[0]
        t = (start + np.arange(n)) / self.rates[port]
        data = np.column_stack([t, block[:, self._columns[port]]])
        with self._lock:
            self._first_host_time.setdefault(port, host_time)
            np.savetxt(self._files[port], data, fmt="%.9g", delimiter=",")
            self._counts[port] = start + n

    def close(self) -> Path:
        with self._lock:
            for f in self._files.values():
                f.close()
            self._files.clear()
        meta = {
            "recording_started_unix": self.started_at,
            "first_data_received_unix": {
                ("emg" if p == protocol.EMG_PORT else "aux"): t for p, t in self._first_host_time.items()
            },
            "samples": {"emg": self._counts[protocol.EMG_PORT], "aux": self._counts[protocol.AUX_PORT]},
            "note": "time_s = sample_index / port_rate, counted from the first sample after START/trigger.",
            "system": self.system.to_dict(),
        }
        path = self.out_dir / "metadata.json"
        path.write_text(json.dumps(meta, indent=2))
        return path
