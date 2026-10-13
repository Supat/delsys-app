"""A small stand-in for the Trigno Control Utility's SDK server.

It speaks the same command protocol and streams synthetic data on the EMG/AUX
ports, so the client can be developed and tested on a Mac without hardware.
Behaviour follows MAN-025-3-5; it is not a byte-exact replica of the TCU.
"""

from __future__ import annotations

import argparse
import logging
import math
import socket
import struct
import threading
import time
from dataclasses import dataclass

from . import protocol

log = logging.getLogger(__name__)

FRAME_INTERVAL = 0.0135
EMG_SAMPLES_PER_FRAME = 27  # 2000 Hz (backwards compatibility + upsampling)
AUX_SAMPLES_PER_FRAME = 2  # 148.15 Hz


@dataclass
class MockSensor:
    slot: int
    type: str = "O"  # Avanti
    mode: int = 9
    aux_units: tuple[str, ...] = ("g", "g", "g", "deg/s", "deg/s", "deg/s")

    @property
    def aux_count(self) -> int:
        return len(self.aux_units)


class MockTrignoServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        sensors: list[MockSensor] | None = None,
        one_based_start_index: bool = True,
        trigger_delay: float = 1.0,
        trigger_duration: float = 2.0,
    ):
        self.host = host
        self.sensors = {s.slot: s for s in (sensors or [MockSensor(1), MockSensor(2, mode=1, aux_units=("g", "g", "g"))])}
        self.one_based = one_based_start_index
        self.trigger_delay = trigger_delay
        self.trigger_duration = trigger_duration
        self.endian = "LITTLE"
        self.trigger_start = False
        self.trigger_stop = False
        self.streaming = threading.Event()
        self._stop = threading.Event()
        self._listeners: list[socket.socket] = []
        self._data_clients: dict[int, list[socket.socket]] = {protocol.EMG_PORT: [], protocol.AUX_PORT: []}
        self._cmd_clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # --- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        for base in (protocol.COMMAND_PORT, protocol.EMG_PORT, protocol.AUX_PORT):
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, base))
            srv.listen()
            srv.settimeout(0.2)
            self._listeners.append(srv)
            self._spawn(self._accept_loop, srv, base)
        self._spawn(self._stream_loop)
        log.info("Mock Trigno server listening on %s:%d-%d", self.host, protocol.COMMAND_PORT, protocol.AUX_PORT)

    def shutdown(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        with self._lock:
            for s in self._listeners + self._cmd_clients + sum(self._data_clients.values(), []):
                s.close()

    def _spawn(self, target, *args) -> None:
        t = threading.Thread(target=target, args=args, daemon=True)
        t.start()
        self._threads.append(t)

    def _accept_loop(self, srv: socket.socket, base: int) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except (socket.timeout, OSError):
                continue
            if base == protocol.COMMAND_PORT:
                with self._lock:
                    self._cmd_clients.append(conn)
                self._spawn(self._command_loop, conn)
            else:
                with self._lock:
                    self._data_clients[base].append(conn)

    # --- command port ----------------------------------------------------------------

    def _command_loop(self, conn: socket.socket) -> None:
        conn.sendall(b"Delsys Trigno System Digital Protocol Version 3.6.0 (mock)" + protocol.TERMINATOR)
        conn.settimeout(0.2)
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while protocol.TERMINATOR in buf:
                raw, buf = buf.split(protocol.TERMINATOR, 1)
                command = raw.decode("ascii").strip().upper()
                reply = self.handle(command)
                conn.sendall(reply.encode("ascii") + protocol.TERMINATOR)
                if reply == "BYE":
                    conn.close()
                    return

    def _broadcast_event(self, message: str) -> None:
        with self._lock:
            for c in self._cmd_clients:
                try:
                    c.sendall(message.encode("ascii") + protocol.TERMINATOR)
                except OSError:
                    pass

    def start_index(self, slot: int) -> int:
        return slot if self.one_based else slot - 1

    def handle(self, cmd: str) -> str:
        words = cmd.split()
        if cmd == "VERSION?":
            return "3.6.0"
        if cmd == "BASE SERIAL?":
            return "BID: MOCK"
        if cmd == "BASE FIRMWARE?":
            return "Firmware: MA2909-BE1503-DS0801-US2004-DA0000"
        if cmd == "FRAME INTERVAL?":
            return str(FRAME_INTERVAL)
        if cmd == "MAX SAMPLES EMG?":
            return str(EMG_SAMPLES_PER_FRAME)
        if cmd == "MAX SAMPLES AUX?":
            return str(AUX_SAMPLES_PER_FRAME)
        if cmd == "BACKWARDS COMPATIBILITY?":
            return "YES"
        if cmd == "UPSAMPLING?":
            return "UPSAMPLING ON"
        if cmd == "ENDIANNESS?":
            return self.endian
        if cmd in ("ENDIAN BIG", "ENDIAN LITTLE"):
            self.endian = words[1]
            return "OK"
        if cmd == "TRIGGER?":
            return f"START {'ON' if self.trigger_start else 'OFF'} STOP {'ON' if self.trigger_stop else 'OFF'}"
        if len(words) == 3 and words[0] == "TRIGGER" and words[1] in ("START", "STOP") and words[2] in ("ON", "OFF"):
            if self.streaming.is_set():
                return protocol.REPLY_CANNOT
            setattr(self, f"trigger_{words[1].lower()}", words[2] == "ON")
            if self.trigger_start and words[1] == "START" and words[2] == "ON":
                self._spawn(self._simulate_triggers)
            return "OK"
        if cmd == "START":
            self.streaming.set()
            return "OK"
        if cmd == "STOP":
            self.streaming.clear()
            return "OK"
        if cmd == "QUIT":
            self.streaming.clear()
            return "BYE"
        if cmd in ("MASTER", "MASTER?", "SLAVE?"):
            return {"MASTER": "NEW MASTER", "MASTER?": "YES", "SLAVE?": "NO"}[cmd]
        if words and words[0] == "SENSOR" and len(words) >= 3 and words[1].isdigit():
            return self._sensor_command(int(words[1]), words[2:])
        return protocol.REPLY_INVALID

    def _sensor_command(self, slot: int, rest: list[str]) -> str:
        if not 1 <= slot <= protocol.MAX_SENSORS:
            return protocol.REPLY_INVALID
        s = self.sensors.get(slot)
        q = " ".join(rest)
        if q == "PAIRED?":
            return "YES" if s else "NO"
        if q == "PAIR":
            self.sensors.setdefault(slot, MockSensor(slot))
            threading.Timer(0.5, self._broadcast_event, args=(f"SENSOR {slot} PAIR COMPLETE",)).start()
            return "PAIR INITIATED, PRESS AND HOLD SENSOR BUTTON TO COMPLETE PAIR"
        if s is None:
            return "NO" if q == "ACTIVE?" else protocol.REPLY_INVALID
        if rest[0] == "SETMODE" and len(rest) == 2 and rest[1].isdigit():
            if self.streaming.is_set():
                return protocol.REPLY_CANNOT
            s.mode = int(rest[1])
            return f"Sensor {slot} set to MODE {s.mode}"
        simple = {
            "ACTIVE?": "YES",
            "TYPE?": s.type,
            "SERIAL?": f"SID-{1000 + slot}",
            "FIRMWARE?": "40-34",
            "MODE?": f"MODE {s.mode}",
            "CHANNELCOUNT?": str(1 + s.aux_count),
            "EMGCHANNELCOUNT?": "1",
            "AUXCHANNELCOUNT?": str(s.aux_count),
            "STARTINDEX?": str(self.start_index(slot)),
            "RANGE?": "HIGH",
            "BANDWIDTH?": "WIDE",
        }
        if q in simple:
            return simple[q]
        if rest[0] in ("SETRANGE", "SETBANDWIDTH") and len(rest) == 2:
            return "OK"
        if rest[0] == "CHANNEL" and len(rest) == 3 and rest[1].isdigit():
            m = int(rest[1])
            if not 1 <= m <= 1 + s.aux_count:
                return protocol.REPLY_INVALID
            emg = m == 1
            return {
                "RATE?": "1925.92592593" if emg else "148.148148148",
                "SAMPLES?": "26" if emg else "2",
                "GAIN?": "300" if emg else "1",
                "UNITS?": "V" if emg else s.aux_units[m - 2],
            }.get(rest[2], protocol.REPLY_INVALID)
        return protocol.REPLY_INVALID

    def _simulate_triggers(self) -> None:
        """Pretend an external device fires the start (and stop) trigger."""
        time.sleep(self.trigger_delay)
        if not self.trigger_start or self._stop.is_set():
            return
        log.info("Mock: start trigger")
        self.streaming.set()
        if self.trigger_stop:
            time.sleep(self.trigger_duration)
            log.info("Mock: stop trigger")
            self.streaming.clear()
            self._broadcast_event("STOPPED")

    # --- data ports ----------------------------------------------------------------------

    def _stream_loop(self) -> None:
        emg_i = aux_i = 0
        next_frame = time.monotonic()
        while not self._stop.is_set():
            if not self.streaming.is_set():
                time.sleep(0.005)
                next_frame = time.monotonic()
                continue
            fmt = ("<" if self.endian == "LITTLE" else ">") + "f"
            emg = []
            for _ in range(EMG_SAMPLES_PER_FRAME):
                t = emg_i / 2000.0
                row = [0.0] * protocol.PORT_CHANNELS[protocol.EMG_PORT]
                for slot in self.sensors:
                    row[slot - 1] = 1e-4 * math.sin(2 * math.pi * 50 * slot * t) * (1 + math.sin(2 * math.pi * 0.5 * t))
                emg.extend(row)
                emg_i += 1
            aux = []
            for _ in range(AUX_SAMPLES_PER_FRAME):
                t = aux_i / (AUX_SAMPLES_PER_FRAME / FRAME_INTERVAL)
                row = [0.0] * protocol.PORT_CHANNELS[protocol.AUX_PORT]
                for slot, s in self.sensors.items():
                    base = (slot - 1) * protocol.AUX_CHANNELS_PER_SLOT
                    for k in range(s.aux_count):
                        row[base + k] = math.sin(2 * math.pi * (2 + k) * t) + slot
                aux.extend(row)
                aux_i += 1
            self._send(protocol.EMG_PORT, struct.pack(f"{fmt[0]}{len(emg)}f", *emg))
            self._send(protocol.AUX_PORT, struct.pack(f"{fmt[0]}{len(aux)}f", *aux))
            next_frame += FRAME_INTERVAL
            time.sleep(max(0.0, next_frame - time.monotonic()))

    def _send(self, base: int, payload: bytes) -> None:
        with self._lock:
            alive = []
            for c in self._data_clients[base]:
                try:
                    c.sendall(payload)
                    alive.append(c)
                except OSError:
                    c.close()
            self._data_clients[base] = alive


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Run a mock Trigno SDK server for development.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--sensors", type=int, default=2, help="number of active sensors (slots 1..N)")
    p.add_argument("--zero-based-start-index", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sensors = [MockSensor(i) for i in range(1, args.sensors + 1)]
    server = MockTrignoServer(args.host, sensors, one_based_start_index=not args.zero_based_start_index)
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
