"""`trigno` command-line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .client import CommandClient, TrignoError
from .recorder import CsvRecorder
from .sensors import format_system, query_system
from .session import TrignoSession

DEFAULT_HOST = os.environ.get("TRIGNO_HOST", "localhost")


def cmd_info(args) -> int:
    with CommandClient(args.host) as client:
        print(format_system(query_system(client, args.start_index_base)))
    return 0


def cmd_raw(args) -> int:
    with CommandClient(args.host) as client:
        for command in args.commands:
            print(f"> {command}\n{client.send(command)}")
    return 0


def cmd_config(args) -> int:
    with CommandClient(args.host) as client:
        for slot in args.sensors:
            if args.mode is not None:
                print(f"Sensor {slot}: {client.command(f'SENSOR {slot} SETMODE {args.mode}')}")
            if args.range:
                print(f"Sensor {slot}: range {client.command(f'SENSOR {slot} SETRANGE {args.range.upper()}')}")
            if args.bandwidth:
                print(f"Sensor {slot}: bandwidth {client.command(f'SENSOR {slot} SETBANDWIDTH {args.bandwidth.upper()}')}")
        if args.backwards_compatibility:
            print("Backwards compatibility:", client.command(f"BACKWARDS COMPATIBILITY {args.backwards_compatibility.upper()}"))
        if args.upsample:
            print("Upsample:", client.command(f"UPSAMPLE {args.upsample.upper()}"))
    return 0


def cmd_pair(args) -> int:
    done = threading.Event()

    def on_event(message: str) -> None:
        if message.endswith("PAIR COMPLETE"):
            print(message)
            done.set()

    with CommandClient(args.host, on_event=on_event) as client:
        print(client.command(f"SENSOR {args.slot} PAIR"))
        print("Turn the sensor on and press and hold its button...")
        if not done.wait(args.timeout):
            print("Pairing did not complete in time.", file=sys.stderr)
            return 1
    return 0


def cmd_record(args) -> int:
    use_trigger = args.trigger_start or args.trigger_stop
    with TrignoSession(args.host, args.start_index_base) as session:
        system = session.system
        print(format_system(system), "\n")
        if not system.sensors:
            print("Nothing to record: no active sensors.", file=sys.stderr)
            return 1

        recorder = None
        if not args.no_save:
            out = Path(args.out or f"recordings/{datetime.now():%Y%m%d-%H%M%S}")
            recorder = CsvRecorder(out, system)
            session.add_sink(recorder.write)

        live = None
        if args.plot:
            from .plot import LivePlot

            live = LivePlot(system, window_s=args.window)
            session.add_sink(live.sink)

        session.arm_triggers(start=args.trigger_start, stop=args.trigger_stop)

        interrupted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: interrupted.set())

        session.start(wait_for_trigger=args.trigger_start)
        if args.trigger_start:
            print("Armed. Waiting for start trigger... (Ctrl+C to cancel)")
        else:
            print("Recording... (Ctrl+C to stop)")

        t0: list[float] = []

        def keep_going() -> bool:
            if interrupted.is_set() or session.stopped_by_trigger.is_set() or session.disconnected.is_set():
                return False
            if any(s.first_data_time for s in session.streams):
                if not t0:
                    t0.append(time.monotonic())
                    if args.trigger_start:
                        print("Triggered, recording.")
                if args.duration and time.monotonic() - t0[0] >= args.duration:
                    return False
            return True

        if live is not None:
            live.run(poll=keep_going)
        else:
            while keep_going():
                time.sleep(0.05)

        if session.stopped_by_trigger.is_set():
            print("Stopped by trigger.")
        session.stop()
        if use_trigger:
            # Leave the base station disarmed for the next user of the TCU.
            session.arm_triggers(start=False, stop=False)

        counts = session.samples_received()
        print("Samples received:", ", ".join(f"port {p}: {n}" for p, n in counts.items()))
        if recorder is not None:
            print(f"Saved to {recorder.close().parent}")
    return 0


def cmd_mock(args) -> int:
    from .mock_server import main

    argv = ["--host", args.bind, "--sensors", str(args.sensors)]
    if args.zero_based_start_index:
        argv.append("--zero-based-start-index")
    main(argv)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trigno", description="Stream Delsys Trigno data via the Trigno SDK server.")
    p.add_argument("--host", default=DEFAULT_HOST, help="IP of the PC running the Trigno Control Utility (env TRIGNO_HOST)")
    p.add_argument("--start-index-base", type=int, choices=(0, 1), default=None,
                   help="override auto-detection of whether STARTINDEX? is 0- or 1-based")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("info", help="show base station, sampling rates and active sensors")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("raw", help="send raw SDK commands, e.g. trigno raw 'SENSOR 1 MODE?'")
    s.add_argument("commands", nargs="+")
    s.set_defaults(func=cmd_raw)

    s = sub.add_parser("config", help="configure sensors / sampling (while not streaming)")
    s.add_argument("sensors", type=int, nargs="*", help="sensor slots (1-16)")
    s.add_argument("--mode", type=int, help="sensor mode number (see manual section 7)")
    s.add_argument("--range", choices=("low", "high"), help="Avanti EMG range: low=±5.5mV, high=±11mV")
    s.add_argument("--bandwidth", choices=("narrow", "wide"), help="Avanti EMG bandwidth: narrow=20-450Hz, wide=10-850Hz")
    s.add_argument("--backwards-compatibility", choices=("on", "off"))
    s.add_argument("--upsample", choices=("on", "off"))
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("pair", help="pair a sensor to a slot")
    s.add_argument("slot", type=int)
    s.add_argument("--timeout", type=float, default=60)
    s.set_defaults(func=cmd_pair)

    s = sub.add_parser("record", help="stream and record data (optionally with live plot / triggers)")
    s.add_argument("-o", "--out", help="output directory (default recordings/<timestamp>)")
    s.add_argument("-d", "--duration", type=float, help="seconds to record after data starts")
    s.add_argument("--plot", action="store_true", help="show live plot")
    s.add_argument("--window", type=float, default=5.0, help="live plot window in seconds")
    s.add_argument("--no-save", action="store_true", help="don't write files (plot only)")
    s.add_argument("--trigger-start", action="store_true", help="wait for hardware start trigger instead of sending START")
    s.add_argument("--trigger-stop", action="store_true", help="stop when the hardware stop trigger arrives")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("mock-server", help="run a fake TCU server for development")
    s.add_argument("--bind", default="127.0.0.1", help="address to listen on")
    s.add_argument("--sensors", type=int, default=2, help="number of active sensors (slots 1..N)")
    s.add_argument("--zero-based-start-index", action="store_true")
    s.set_defaults(func=cmd_mock)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except TrignoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
