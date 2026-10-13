# trigno — Delsys Trigno SDK client for macOS

> Apps with the same features:
> - **iPad + Mac** (SwiftUI): [`apple/`](apple/README.md)
> - **Windows 7 SP1 and later** (PyQt5, `trigno-gui`): [`windows/`](windows/README.md)

Reads EMG and IMU/aux data digitally from a Delsys Trigno system through the
Trigno SDK server (TCP), instead of sampling the base station's analog outputs
with a DAQ. Built from *MAN-025-3-5 Trigno SDK User Guide* (SDK v3.5).

```
Trigno sensors ──RF──▶ Base station ──USB──▶ Windows PC running TCU / SDK Server
                                                     │  TCP 50040 (commands)
                                                     │  TCP 50043 (EMG, 16 ch)
                                                     │  TCP 50044 (AUX, 144 ch)
                                                     ▼
                                              Mac running `trigno`
```

The base station driver and the Trigno Control Utility (TCU) only run on
Windows, so a Windows machine (or VM) is still required to host the server.
Everything else runs on the Mac.

## 1. Windows side (once)

1. Install the **Delsys SDK Server** (standalone TCU) from
   <https://delsys.com/support/trigno-sdk/> and plug in the base station.
2. Open the TCU, pair the sensors to slots, and leave it running.
3. Allow inbound TCP **50040–50044** in Windows Defender Firewall.
4. Note the PC's IP address (`ipconfig`). Use a wired network or a direct
   Ethernet cable if possible: EMG at 2 kHz × 16 ch is only about 128 kB/s, but
   Wi-Fi adds jitter.

A Windows VM on the Mac also works if the base station's USB passes through.
That's reliable on Intel Macs. On Apple Silicon (Windows 11 ARM) it depends on
whether Delsys's USB driver works under ARM, so test it before relying on it.

## 2. Mac side

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[plot,lsl]'
export TRIGNO_HOST=192.168.1.50      # IP of the Windows PC
.venv/bin/trigno info                 # base station, rates, sensors, channel map
```

## Usage

```bash
trigno info                                   # list active sensors and where each channel lands
trigno record -d 30                           # record 30 s to recordings/<timestamp>/
trigno record --plot                          # record + live plot, Ctrl+C or close window to stop
trigno record --plot --no-save                # just watch the signals
trigno record --trigger-start --trigger-stop  # start/stop on the hardware trigger inputs
trigno config 1 2 --mode 9                    # set sensors 1 and 2 to mode 9 (EMG + IMU)
trigno config 1 --range high --bandwidth wide # Avanti: ±11 mV, 10–850 Hz
trigno config --backwards-compatibility off   # let port rates follow sensors' native rates
trigno pair 3                                 # pair a sensor to slot 3
trigno raw "SENSOR 1 MODE?" "TRIGGER?"        # any SDK command, verbatim
```

Configuration commands are rejected (`CANNOT COMPLETE`) while streaming.
Sensor mode numbers are listed in section 7 of the manual.

### Output

`recordings/<timestamp>/`:

- `emg.csv`: `time_s` followed by one column per EMG channel (e.g. `S1_emg1_V`),
  at the EMG port rate.
- `aux.csv`: `time_s` followed by accelerometer/gyro/etc. columns
  (e.g. `S1_aux1_g`), at the AUX port rate.
- `metadata.json`: server/base versions, port rates, the per-sensor channel
  map (type, mode, gain, units, native rate), sample counts, and the host
  wall-clock time when data first arrived.

`time_s` is `sample_index / port_rate` from the first sample after START (or the
start trigger). It doesn't come from the host clock, so network jitter doesn't
affect it.

### Python API

```python
from trigno import TrignoSession

with TrignoSession("192.168.1.50") as s:
    print(s.system.sensors)
    s.add_sink(lambda port, block, start, t: print(port, block.shape))  # block: (samples, channels)
    s.start()
    ...
    s.stop()
```

## Moving from the DAQ setup

- **Sampling rate.** With backwards compatibility ON (default) the EMG port is
  2000 Hz (upsampled) and AUX is 148.1 Hz. Turn upsampling or backwards
  compatibility off to get the sensors' native rates. `trigno info` prints the
  effective rates.
- **Units.** The data is already in sensor units (V for EMG, g, deg/s, …), so no
  DAQ gain/offset conversion is needed.
- **Synchronisation with other equipment.** This is the one thing the DAQ gave
  you for free: anything else wired into the same DAQ was sample-aligned with
  EMG. Over the SDK, align recordings with the base station's start/stop
  trigger inputs (`--trigger-start/--trigger-stop`), driven from or shared with
  the other device. Host timestamps in `metadata.json` are only good to roughly
  10 ms.

## Development without hardware

`trigno mock-server` emulates the TCU's SDK server on localhost (command
protocol, synthetic EMG/AUX streams, and simulated triggers):

```bash
trigno mock-server --sensors 3 &
TRIGNO_HOST=127.0.0.1 trigno record --plot
.venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

## Things to verify on the real system

The manual is ambiguous in a few places. The code handles each case, but check
these the first time you connect:

- **`STARTINDEX?` base.** Section 6.1.3 shows it 0-based, while the appendix
  example is 1-based. The client auto-detects it from the replies. If the
  channel map in `trigno info` looks shifted, force it with
  `--start-index-base 0|1`.
- **Trigger start.** Section 4 says to arm the trigger *instead of* sending
  `START`, and that's what `--trigger-start` does. If nothing streams after the
  trigger fires, change `TrignoSession.start` so it sends `START` after arming
  as well.
- **`SETRANGE` / `SETBANDWIDTH` syntax.** These are sent as
  `SENSOR n SETRANGE HIGH`. If the TCU answers `INVALID COMMAND`, try variants
  with `trigno raw`.
