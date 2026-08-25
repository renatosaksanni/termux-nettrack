# nettrack

Terminal cellular monitor and drive-test logger for **Termux** on Android.
Pure Python standard library: no pip, no root, no dependencies.

```
 nettrack ●  Telkomsel  LTE                        17:08:13  83% 32°C
── SERVING CELL ──────────────────────────────────────────────────────
  LTE   B3 / 1850.0 MHz   FDD
 CGI       510-10-4521-315905
 Cell ID   315905   node 1234 / sector 1
 PCI/PSC   301      ARFCN 1650      BW 20 MHz
── SIGNAL ────────────────────────────────────────────────────────────
 RSRP     -72.0 dBm █████████████████████████████▋░░░░░░ Excellent
       hist ▇▆▆▆▆▆▆▆▆▆▆
 RSRQ     -13.6 dB  ███████████████████▉░░░░░░░░░░░░░░░░
 SINR      21.3 dB  ████████████████████████████▉░░░░░░░
 TA 15 (~1.17 km)   ASU 67   bars 4/4
```

## Install

Run from the native Termux shell, not from proot-distro:

```bash
cd nettrack && ./install.sh
nettrack doctor
```

`nettrack --sim` runs the whole interface on synthetic data if you just want
to look around.

## Backends

nettrack reads the radio through one of two backends, picked automatically.
Force one with `--source termux` or `--source adb`.

**termux** — needs the **Termux:API app**, not just `pkg install termux-api`,
installed from the same source as Termux, with Location permission set to
*Allow all the time*. Android returns an empty cell list otherwise.

The **Google Play build of Termux does not work**: its API scripts are stubs
that exit 0 and print a notice instead of JSON, so telephony, Wi-Fi and
location are all missing. Use the adb backend, or reinstall Termux and
Termux:API from F-Droid or GitHub.

**adb** — Wireless debugging (Android 11+) lets the phone hand itself a
`shell` uid over loopback, with no root and no PC. On Samsung, **Auto Blocker
must stay off** while you use this backend; switching it on blocks adb and
drops the connection.

```bash
pkg install android-tools
nettrack adb setup      # step-by-step guide
```

It reads `dumpsys`, which reports the band the modem is actually using. That
settles the NR-ARFCN ranges overlapping between bands, which no lookup table
can resolve alone.

## Commands

```bash
nettrack                      # live dashboard
nettrack doctor               # check the environment
nettrack cells [--json]       # one-shot cell dump, for scripting
nettrack log --duration 1800  # headless logging
nettrack speed                # latency and throughput test
nettrack summary drive.csv    # session statistics
nettrack export drive.csv     # KML for Google Earth
nettrack band 1650 632628     # offline ARFCN lookup
nettrack raw                  # verbatim termux-api output, for diagnosis
nettrack adb status           # adb backend state
```

The banner appears on start-up; `--no-banner` suppresses it. Commands meant
for scripting (`cells`, `band`, `export`, `summary`) never print it.

Dashboard keys: `1` dashboard, `2` cells, `3` Wi-Fi, `4` events, `s` speed
test, `p` ping, `L` toggle logging, `h` help, `q` quit.

## Drive test

```bash
nettrack log -i 1 --cellfile sites.csv --raw \
             --alarm-rsrp -110 --vibrate --logdir /sdcard/nettrack
```

Each session writes three files:

- `NAME.csv` — one row per sample, 46 columns, G-NetTrack column names
- `NAME.jsonl` — raw modem JSON (`--raw`), for auditing
- `NAME.events.log` — handovers, band and technology changes, alarms

## Cell file

A CSV of known sites lets nettrack compute distance, azimuth and the angle off
the antenna boresight. Comma, semicolon or tab separated; column names are
matched loosely.

```csv
Cellname,CID,PCI,ARFCN,Latitude,Longitude,Azimuth,Height
JKT_MENTENG_1,315905,301,1650,-6.198500,106.815000,120,32
```

Sites are matched by CGI, then by cell ID (including the eNB id after an
8-bit shift), then by the (ARFCN, PCI) pair.

## What it measures

- **Serving cell** — technology, operator, MCC/MNC, LAC/TAC, cell ID split into
  node and sector, PCI/PSC/BSIC, ARFCN, bandwidth, plus band and frequency
  derived per 3GPP (TS 45.005, 25.101, 36.101, 38.104).
- **Signal** — RSRP, RSRQ, SINR, CQI, timing advance with a distance estimate;
  RSCP and EcNo on 3G, RSSI on 2G, SS-RSRP/RSRQ/SINR on 5G NR.
- **Neighbours** — every cell the modem reports, sorted by level, with the
  delta against the serving cell.
- **Location** — position, accuracy, speed, heading, odometer, and the relation
  to the matched site.
- **Wi-Fi** — active link and scan results, band and channel derived from
  frequency.

## Limits

- **Throughput is generated, not observed.** Android blocks `/proc/net/*` for
  untrusted apps, so the device-wide byte counters a native app reads via
  `TrafficStats` are unreachable from a shell. `nettrack speed` makes its own
  traffic and times it, so it **uses mobile data**; the bytes spent are always
  reported.
- **The modem sets the sample rate.** Android throttles `getAllCellInfo` to
  about once a second, so lowering `-i` past that yields nothing new.
- **Some fields are vendor dependent.** CQI and timing advance often arrive as
  `Integer.MAX_VALUE`, meaning unavailable; nettrack leaves those blank rather
  than inventing a number.
- **GPS on the adb backend can be stale.** `dumpsys location` exposes only the
  last known fix, which goes cold when nothing else requests location. Its age
  is reported so you can judge it.
- **proot-distro usually works.** termux-api reaches the Termux:API app over a
  socket, unlike direct execution of Android binaries. nettrack warns, then
  lets the live probe decide.

## Troubleshooting

`nettrack raw` prints each tool's output verbatim — exit code, byte counts,
stdout and stderr. It is the fastest way to tell a missing permission apart
from a missing API.

| Symptom | Cause |
|---------|-------|
| `not yet available on Google Play` | Play Store build — use the adb backend or reinstall from F-Droid |
| empty list from cellinfo | Termux:API lacks Location permission |
| `timed out` | Termux:API app not installed, or battery-restricted |
| `not found` | run `pkg install termux-api` |

## Checks

`compileall` proves only that the syntax parses. Before committing, run:

```bash
python3 scripts/check.py
```

It scans every module for names used but never imported, then smoke-runs each
subcommand and fails on any traceback.

## Layout

```
bands.py   ARFCN <-> frequency/band (GSM, UMTS, LTE, NR)
geo.py     haversine, azimuth, cell file, odometer
model.py   normalises backend JSON into one stable schema
api.py     termux-api wrappers, threaded pollers
adbsrc.py  adb backend: dumpsys parsed into the same shape
engine.py  collectors, event detection, session state
netperf.py latency, throughput, DNS
alarms.py  thresholds with hysteresis
store.py   CSV, JSONL, KML output
ui.py      ANSI toolkit; views.py screens; app.py TUI loop
cli.py     arguments and subcommands
banner.py  start-up wordmark; sim.py synthetic source for --sim
```
