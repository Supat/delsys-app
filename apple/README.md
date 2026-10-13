# Trigno for iPad and Mac

A SwiftUI app that streams Delsys Trigno EMG/IMU data from the Trigno SDK server
(the Trigno Control Utility on a Windows PC) over the network. It does the same
things as the Python `trigno` CLI:

- **Connect** to the TCU by IP, list the base station, sampling rates and
  active sensors with their channel map.
- **Record** to CSV + `metadata.json`, in the same layout as `trigno record`.
- **Live plot** of every sensor: EMG per channel, and AUX grouped by units
  (g, deg/s, …).
- **Hardware triggers**: wait for the start trigger, stop on the stop trigger.
- **Sensor config**: mode, Avanti range and bandwidth, backwards compatibility,
  upsampling, and pairing.
- **Console** for raw SDK commands.

```
apple/
  project.yml        XcodeGen spec (source of truth for the Xcode project)
  Trigno.xcodeproj   generated, run `xcodegen generate` after editing project.yml
  TrignoKit/         Swift package: protocol, networking, discovery, recorder (+ tests)
  TrignoApp/         SwiftUI app (iPad + macOS)
```

## Build and install

1. Set your team ID in `project.yml` (`DEVELOPMENT_TEAM`), then run
   `xcodegen generate`. Or pick the team in Xcode › Trigno target › Signing.
2. Open `Trigno.xcodeproj`, select your iPad (or "My Mac") and Run.
3. To install on lab iPads without the App Store, use Product › Archive ›
   Distribute App › TestFlight (internal testing) or Release Testing (ad hoc).

The bundle ID is `com.supatsaetia.trigno`. Change it in `project.yml` if you
like.

## Using it on the iPad

- Put the iPad on the same network as the Windows PC running the TCU. A USB-C
  Ethernet adapter gives steadier streaming than Wi-Fi. Allow TCP
  50040–50044 in the Windows firewall.
- The first time you connect, iPadOS asks for **Local Network** permission.
  Allow it. If you denied it, re-enable it in Settings › Privacy & Security ›
  Local Network.
- **Keep the app on screen while recording.** iPadOS suspends background apps,
  which cuts the TCP streams. The screen won't auto-lock while acquiring. If the
  app is killed mid-recording, the next Record sends `STOP` first, which clears
  the stale acquisition on the server.
- Recordings live in the Files app under **On My iPad › Trigno › Recordings**.
  You can also share them (AirDrop, Mail, …) from Recordings in the app. On the
  Mac they're in
  `~/Library/Containers/com.supatsaetia.trigno/Data/Documents/Recordings`
  (the folder button in Recordings opens it in Finder).

## Development without hardware

The Python mock server from the repo root stands in for the TCU:

```bash
.venv/bin/trigno mock-server --sensors 3                    # simulator / Mac: host 127.0.0.1
.venv/bin/trigno mock-server --bind 0.0.0.0 --sensors 3     # real iPad: use the Mac's IP
```

The debug build accepts launch arguments to skip tapping (Xcode › Scheme ›
Run › Arguments):
`-host 127.0.0.1 -autoConnect YES -autoStart YES [-durationLimit 3] [-triggerStart YES] [-triggerStop YES]`

Tests (they start the mock themselves and need the repo's `.venv`):

```bash
cd apple/TrignoKit && swift test
```
