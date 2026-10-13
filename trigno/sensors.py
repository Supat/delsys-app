"""Sensor discovery and configuration via SDK queries (sections 6.3, 8)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from . import protocol
from .client import CommandClient

log = logging.getLogger(__name__)


@dataclass
class ChannelInfo:
    number: int  # 1-based channel number on the sensor ("SENSOR n CHANNEL m")
    port: str  # "emg" or "aux"
    column: int  # 0-based position in that port's multiplexed frame
    rate: float | None
    units: str
    gain: float | None
    label: str


@dataclass
class SensorInfo:
    slot: int
    type: str
    serial: str
    firmware: str
    mode: str
    emg_channel_count: int
    aux_channel_count: int
    start_index: int  # 0-based, normalised
    channels: list[ChannelInfo] = field(default_factory=list)


@dataclass
class SystemInfo:
    version: str
    base_serial: str
    base_firmware: str
    frame_interval: float
    max_samples_emg: int
    max_samples_aux: int
    backwards_compatibility: str
    upsampling: str
    endianness: str
    trigger: str
    sensors: list[SensorInfo] = field(default_factory=list)

    @property
    def emg_rate(self) -> float:
        return self.max_samples_emg / self.frame_interval

    @property
    def aux_rate(self) -> float:
        return self.max_samples_aux / self.frame_interval

    def to_dict(self) -> dict:
        d = asdict(self)
        d["emg_rate"] = self.emg_rate
        d["aux_rate"] = self.aux_rate
        return d


def _float(text: str) -> float | None:
    try:
        return float(text.split()[-1])
    except (ValueError, IndexError):
        return None


def _int(text: str) -> int:
    value = _float(text)
    return int(value) if value is not None else 0


def _yes(text: str) -> bool:
    return text.strip().upper() == "YES"


def detect_start_index_base(raw_indices: dict[int, int]) -> int:
    """Decide whether STARTINDEX replies are 0- or 1-based.

    The manual contradicts itself: section 6.1.3 shows slot 1 -> "0", while the
    appendix example shows slot 1 -> "1". Resolve it from the replies themselves.
    """
    if not raw_indices:
        return 0
    values = raw_indices.values()
    if min(values) == 0:
        return 0
    if max(values) >= protocol.PORT_CHANNELS[protocol.EMG_PORT]:
        return 1
    # Without multi-channel sensors the index tracks the slot: slot n -> n-1 or n.
    if all(index == slot for slot, index in raw_indices.items()):
        return 1
    if all(index == slot - 1 for slot, index in raw_indices.items()):
        return 0
    log.warning("Could not infer STARTINDEX base from %s; assuming 1-based", raw_indices)
    return 1


def query_system(client: CommandClient, start_index_base: int | None = None) -> SystemInfo:
    q = client.send
    info = SystemInfo(
        version=client.version or q("VERSION?"),
        base_serial=q("BASE SERIAL?"),
        base_firmware=q("BASE FIRMWARE?"),
        frame_interval=_float(q("FRAME INTERVAL?")) or 0.0135,
        max_samples_emg=_int(q("MAX SAMPLES EMG?")),
        max_samples_aux=_int(q("MAX SAMPLES AUX?")),
        backwards_compatibility=q("BACKWARDS COMPATIBILITY?"),
        upsampling=q("UPSAMPLING?"),
        endianness=q("ENDIANNESS?"),
        trigger=q("TRIGGER?"),
    )

    raw: dict[int, dict] = {}
    for slot in range(1, protocol.MAX_SENSORS + 1):
        if not _yes(q(f"SENSOR {slot} PAIRED?")):
            continue
        if not _yes(q(f"SENSOR {slot} ACTIVE?")):
            log.info("Sensor %d is paired but not active (turned off?)", slot)
            continue
        raw[slot] = {
            "type": q(f"SENSOR {slot} TYPE?"),
            "serial": q(f"SENSOR {slot} SERIAL?"),
            "firmware": q(f"SENSOR {slot} FIRMWARE?"),
            "mode": q(f"SENSOR {slot} MODE?"),
            "emg": _int(q(f"SENSOR {slot} EMGCHANNELCOUNT?")),
            "aux": _int(q(f"SENSOR {slot} AUXCHANNELCOUNT?")),
            "start": _int(q(f"SENSOR {slot} STARTINDEX?")),
        }

    if start_index_base is None:
        start_index_base = detect_start_index_base({s: r["start"] for s, r in raw.items()})

    for slot, r in raw.items():
        start = r["start"] - start_index_base
        sensor = SensorInfo(
            slot=slot,
            type=r["type"],
            serial=r["serial"],
            firmware=r["firmware"],
            mode=r["mode"],
            emg_channel_count=r["emg"],
            aux_channel_count=r["aux"],
            start_index=start,
        )
        aux_start = start * protocol.AUX_CHANNELS_PER_SLOT
        for m in range(1, r["emg"] + r["aux"] + 1):
            is_emg = m <= r["emg"]
            units = q(f"SENSOR {slot} CHANNEL {m} UNITS?")
            port_name = "emg" if is_emg else "aux"
            column = start + m - 1 if is_emg else aux_start + (m - r["emg"] - 1)
            sensor.channels.append(
                ChannelInfo(
                    number=m,
                    port=port_name,
                    column=column,
                    rate=_float(q(f"SENSOR {slot} CHANNEL {m} RATE?")),
                    units=units,
                    gain=_float(q(f"SENSOR {slot} CHANNEL {m} GAIN?")),
                    label=f"S{slot}_{port_name}{m if is_emg else m - r['emg']}_{units.replace(' ', '')}",
                )
            )
        info.sensors.append(sensor)
    return info


def format_system(info: SystemInfo) -> str:
    lines = [
        f"Server:      {info.version}",
        f"Base:        {info.base_serial}  {info.base_firmware}",
        f"Frame:       {info.frame_interval * 1000:.1f} ms",
        f"EMG port:    {info.emg_rate:.2f} Hz ({info.max_samples_emg} samples/frame)",
        f"AUX port:    {info.aux_rate:.2f} Hz ({info.max_samples_aux} samples/frame)",
        f"Compat:      backwards compatibility={info.backwards_compatibility}, {info.upsampling}",
        f"Endianness:  {info.endianness}",
        f"Triggers:    {info.trigger}",
        "",
    ]
    if not info.sensors:
        lines.append("No active sensors. Pair them in the TCU (or `trigno pair N`) and turn them on.")
    for s in info.sensors:
        lines.append(
            f"Sensor {s.slot:>2}: type {s.type}  {s.serial}  fw {s.firmware}  {s.mode}"
            f"  [EMG x{s.emg_channel_count}, AUX x{s.aux_channel_count}, start {s.start_index}]"
        )
        for c in s.channels:
            rate = f"{c.rate:.2f} Hz" if c.rate else "?"
            lines.append(f"    ch{c.number:<2} {c.port}[{c.column:>3}]  {rate:>12}  {c.units}  gain {c.gain}")
    return "\n".join(lines)
