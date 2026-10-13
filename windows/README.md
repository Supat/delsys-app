# Trigno for Windows (Windows 7 SP1 and later)

A desktop app with the same features as the iPad/Mac app:

- **Connect** to the Trigno Control Utility (TCU) and list the base station,
  sampling rates and active sensors.
- **Record** to CSV + `metadata.json` (same format as the Mac app and
  `trigno record`). The duration can be fixed or open-ended.
- **Live plot**: EMG per channel, and AUX grouped by units.
- **Hardware triggers**: wait for the start trigger, stop on the stop trigger.
- **Sensor and system settings**: mode, Avanti range and bandwidth, backwards
  compatibility, upsampling, and pairing.
- **Lab Streaming Layer** output. The defaults match `Daq_revised.py`
  (`Delsys` / `EMG Signal` / `EMG_Signal`, 16 channels).
- **Console** for raw SDK commands.

It is written in Python with PyQt5 (Qt 5.15). That's the newest stack that
still runs on Windows 7: Python 3.9+ and Qt 6 need Windows 8/10.

## Option A: build a standalone `Trigno.exe` (recommended)

On any 64-bit Windows PC (7 or later), preferably Windows 10/11 with internet access:

1. Double-click `windows\build_exe.bat`. If Python 3.8 isn't installed, it offers
   to install it (see [Python 3.8](#python-38) below).
2. Copy the whole `dist\Trigno` folder to the measurement PC.
3. Run `Trigno.exe`. Python is not needed on the measurement PC.

Build with Python 3.8 even on a newer Windows. A build made with a newer
Python won't start on Windows 7.

## Option B: run from source

On the measurement PC, which already has Python for `Daq_revised.py`:

Double-click `windows\run_from_source.bat`. On the first run it finds or
installs Python 3.8, then installs the dependencies listed in
`requirements-win7.txt` into `.venv-win`.

## Python 3.8

Both scripts look for Python 3.8 with `py -3.8`, then in
`%LOCALAPPDATA%\Programs\Python\Python38`. If it isn't in either place, they ask, then:

1. Download the official Python 3.8.10 (64-bit) installer from python.org.
2. Verify its SHA-256 checksum.
3. Install it silently for the current user, without admin rights. PATH, the
   `py` launcher, `.py` file associations and other Python installs are left
   unchanged.

On Windows 7 the download can fail, because python.org requires TLS 1.2, which
needs .NET Framework 4.5 or later. The installer itself needs SP1 and update
KB2533623. If the download fails, copy
[python-3.8.10-amd64.exe](https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe)
from another PC and install it by hand. Or skip Python on that PC entirely
and use Option A: build there on Windows 10/11, then copy `dist\Trigno` over.

## Using it

- **Server IP**
  - On the same PC as the TCU: `127.0.0.1`.
  - From another PC: the TCU PC's LAN address, from `ipconfig` on that PC.
    The TCU's "Digital data server IP" field may show the address of a
    different, disconnected network adapter.
- **One client at a time.** The TCU serves each data port to one client only.
  Close other programs connected to the TCU first. If a program is connected
  but receives no data, this is the usual reason.
- **Windows Firewall.** The first time you enable LSL, Windows asks whether
  Trigno may communicate on the network. Allow it on Private/Work networks,
  or LSL receivers on other PCs won't find the stream.
- **Recordings** are saved to `Documents\Trigno\Recordings\<date-time>\`.
  "Open recordings folder" opens it in Explorer.
- **Command line.** `Trigno.exe --host 192.168.11.7 --connect` connects on
  launch. Add `--record --duration 60 --lsl` to start recording right away.

## Requirements and limits

- **Windows 7 needs SP1** plus the Universal C Runtime update
  ([KB2999226](https://support.microsoft.com/kb/2999226)). These are already
  present wherever Python 3.8 is installed. The LSL library also needs the
  Visual C++ 2015+ runtime; the lab PC already has it because
  `Daq_revised.py` uses pylsl.
- **Testing status.** The app is tested on macOS with the Windows 7 stack
  (Python 3.8, Qt 5.15, pylsl 1.16) against a mock TCU, and the PyInstaller
  build was dry-run with the same versions. It has not yet been run on a real
  Windows 7 machine.
- **LSL magnitudes.** The SDK reports EMG in volts at the electrode, while
  the DAQ recorded the base's amplified analog output. To match the old
  values, set **EMG scale** in LSL settings to the analog-output gain.
