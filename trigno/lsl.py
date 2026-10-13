"""Lab Streaming Layer output (requires pylsl). Mirrors TrignoLSL in the iPad/Mac app."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

import numpy as np

from . import protocol
from .sensors import SystemInfo

DAQ_COMPATIBLE = "daqCompatible"
ACTIVE_CHANNELS = "activeChannels"


@dataclass
class LSLConfiguration:
    # Defaults match the lab's Daq_revised.py outlet so existing consumers find it unchanged.
    name: str = "Delsys"
    type: str = "EMG Signal"
    source_id: str = "EMG_Signal"
    # DAQ_COMPATIBLE: all 16 EMG columns (channel n = sensor slot n, zeros for empty slots).
    # ACTIVE_CHANNELS: only active sensors' EMG channels, as in emg.csv.
    layout: str = DAQ_COMPATIBLE
    include_aux: bool = False
    # The SDK reports volts at the electrode; the DAQ sampled the base's amplified
    # analog outputs. Set this to that gain if consumers expect the old magnitudes.
    emg_scale: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LSLConfiguration:
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})


def library_version() -> str:
    import pylsl

    v = pylsl.library_version()
    return f"{v // 100}.{v % 100}"


class LSLStreamer:
    """Publishes data blocks as LSL outlets. Create on Start, close on Stop."""

    def __init__(self, system: SystemInfo, config: LSLConfiguration):
        import pylsl

        self.config = config
        self._lock = threading.Lock()

        if config.layout == DAQ_COMPATIBLE:
            by_column = {c.column: c for s in system.sensors for c in s.channels if c.port == "emg"}
            emg = []
            for column in range(protocol.PORT_CHANNELS[protocol.EMG_PORT]):
                c = by_column.get(column)
                emg.append((column, c.label, c.units) if c else (column, f"EMG{column + 1}_unused", "V"))
        else:
            emg = [(c.column, c.label, c.units) for s in system.sensors for c in s.channels if c.port == "emg"]
        self.emg_labels = [label for _, label, _ in emg]
        self._emg = self._make_outlet(pylsl, config.name, config.type, config.source_id,
                                      system.emg_rate, emg, "EMG", config.emg_scale, system)
        self._emg_columns = [col for col, _, _ in emg]

        aux = [(c.column, c.label, c.units) for s in system.sensors for c in s.channels if c.port == "aux"]
        if config.include_aux and aux:
            self._aux = self._make_outlet(pylsl, f"{config.name} AUX", "AUX", f"{config.source_id}_AUX",
                                          system.aux_rate, aux, "AUX", 1.0, system)
            self._aux_columns = [col for col, _, _ in aux]
            self.aux_labels = [label for _, label, _ in aux]
        else:
            self._aux = None
            self._aux_columns = []
            self.aux_labels = []

    @staticmethod
    def _make_outlet(pylsl, name, type_, source_id, rate, channels, channel_type, scale, system):
        info = pylsl.StreamInfo(name, type_, len(channels), rate, "float32", source_id)
        xml_channels = info.desc().append_child("channels")
        for _, label, units in channels:
            ch = xml_channels.append_child("channel")
            ch.append_child_value("label", label)
            ch.append_child_value("unit", units)
            ch.append_child_value("type", channel_type)
        acq = info.desc().append_child("acquisition")
        acq.append_child_value("manufacturer", "Delsys")
        acq.append_child_value("model", "Trigno")
        acq.append_child_value("transport", "Trigno SDK (TCP)")
        acq.append_child_value("base_serial", system.base_serial)
        acq.append_child_value("scale", str(scale))
        return pylsl.StreamOutlet(info, 0, 360)

    def push(self, port: int, block: np.ndarray, start: int, host_time: float) -> None:
        """Data sink: (port, samples[n, port channels], first index, host time)."""
        if self._emg is None:
            return
        if port == protocol.EMG_PORT:
            outlet, columns, scale = self._emg, self._emg_columns, self.config.emg_scale
        elif port == protocol.AUX_PORT and self._aux is not None:
            outlet, columns, scale = self._aux, self._aux_columns, 1.0
        else:
            return
        chunk = np.ascontiguousarray(block[:, columns] * np.float32(scale), dtype=np.float32)
        with self._lock:
            # Timestamped with the LSL clock on arrival, like Daq_revised.py's push_chunk.
            outlet.push_chunk(chunk)

    @property
    def has_consumers(self) -> bool:
        return bool(self._emg.have_consumers())

    def close(self) -> None:
        # Dropping the outlets ends the streams for consumers.
        self._emg = None
        self._aux = None
