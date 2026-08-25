"""Thin, defensive wrappers around the termux-api command line tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

TERMUX_PREFIX = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")


class ApiError(Exception):
    pass


# The Google Play build of Termux ships stub scripts for most of the API:
# they exit 0 and print this notice instead of JSON. Detecting it by name
# turns a baffling parse error into an actionable one.
PLAY_STUB = "not yet available on Google Play"
PLAY_STUB_MSG = (
    "this Termux build (Google Play) does not implement the API. "
    "Install Termux and Termux:API from F-Droid or GitHub instead")


def is_play_stub(text):
    return bool(text) and PLAY_STUB in text


def in_proot():
    """True when running inside proot-distro, where Android binaries cannot
    be exec'd and so termux-api is unreachable."""
    try:
        with open("/proc/version", "r") as fh:
            if "PRoot" in fh.read():
                return True
    except OSError:
        pass
    return os.path.isdir("/etc/apt") and not os.path.isdir(TERMUX_PREFIX + "/bin")


def have(cmd):
    return shutil.which(cmd) is not None or os.path.exists(os.path.join(TERMUX_PREFIX, "bin", cmd))


def _resolve(cmd):
    p = shutil.which(cmd)
    if p:
        return p
    p = os.path.join(TERMUX_PREFIX, "bin", cmd)
    return p if os.path.exists(p) else cmd


def run(cmd, args=(), timeout=10.0):
    """Run a termux-api tool. Returns (stdout, stderr). Raises ApiError."""
    argv = [_resolve(cmd)] + [str(a) for a in args]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ApiError("%s not found - run: pkg install termux-api" % cmd)
    except subprocess.TimeoutExpired:
        raise ApiError("%s timed out after %.0fs - is the Termux:API app "
                       "installed and allowed to run in background?" % (cmd, timeout))
    except PermissionError:
        raise ApiError("%s not executable here (running inside proot?)" % cmd)
    except OSError as e:
        raise ApiError("%s failed: %s" % (cmd, e))
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        raise ApiError("%s exited %d: %s" % (cmd, p.returncode, err[0] if err else "no output"))
    return (p.stdout, p.stderr)


_MISSING = object()


def _find_json(text, max_starts=40):
    """Pull the first complete JSON value out of noisy output.

    Termux:API tools sometimes prefix a warning line, emit a trailing
    newline-separated notice, or interleave a helper message. Scanning for
    the first position that decodes cleanly survives all of those without
    guessing at the specific wording.
    """
    dec = json.JSONDecoder()
    tried = 0
    for i, ch in enumerate(text):
        if ch not in "[{":
            continue
        tried += 1
        if tried > max_starts:
            break
        try:
            value, _end = dec.raw_decode(text, i)
            return value
        except ValueError:
            continue
    return _MISSING


# Keeps the most recent raw output per tool so `nettrack raw` and the error
# messages can show what actually came back rather than a bare complaint.
LAST_RAW = {}


def run_json(cmd, args=(), timeout=10.0, default=None):
    """Run a tool and parse its JSON. Returns (value, error_string)."""
    try:
        out, err = run(cmd, args, timeout)
    except ApiError as e:
        LAST_RAW[cmd] = {"stdout": "", "stderr": "", "error": str(e)}
        return (default, str(e))
    LAST_RAW[cmd] = {"stdout": out, "stderr": err, "error": None}

    txt = out.strip()
    if not txt:
        hint = ""
        if err and err.strip():
            hint = " (stderr: %s)" % err.strip().splitlines()[0][:80]
        return (default, "%s returned no data%s" % (cmd, hint))

    if is_play_stub(txt):
        return (default, "%s: %s" % (cmd, PLAY_STUB_MSG))

    value = _find_json(txt)
    if value is not _MISSING:
        return (value, None)

    snippet = " ".join(txt.split())[:110]
    return (default, "%s: could not parse output: %s" % (cmd, snippet))


# ---------------------------------------------------------------- telephony
def cellinfo(timeout=10.0):
    return run_json("termux-telephony-cellinfo", timeout=timeout, default=[])


def deviceinfo(timeout=10.0):
    return run_json("termux-telephony-deviceinfo", timeout=timeout, default={})


# ---------------------------------------------------------------- location
def location(provider="gps", request="last", timeout=25.0):
    return run_json("termux-location", ["-p", provider, "-r", request],
                    timeout=timeout, default=None)


# ---------------------------------------------------------------- wifi
def wifi_connection(timeout=10.0):
    return run_json("termux-wifi-connectioninfo", timeout=timeout, default={})


def wifi_scan(timeout=20.0):
    return run_json("termux-wifi-scaninfo", timeout=timeout, default=[])


# ---------------------------------------------------------------- misc
def battery(timeout=10.0):
    return run_json("termux-battery-status", timeout=timeout, default={})


def toast(msg, timeout=5.0):
    try:
        run("termux-toast", ["-s", str(msg)], timeout)
    except ApiError:
        pass


def vibrate(ms=250, timeout=5.0):
    try:
        run("termux-vibrate", ["-d", str(int(ms)), "-f"], timeout)
    except ApiError:
        pass


def speak(text, timeout=15.0):
    try:
        run("termux-tts-speak", [str(text)], timeout)
    except ApiError:
        pass


def notify(title, content, notif_id="nettrack", timeout=8.0):
    try:
        run("termux-notification",
            ["-t", str(title), "-c", str(content), "-i", str(notif_id)], timeout)
    except ApiError:
        pass


class WakeLock:
    """Keeps the CPU awake for the duration of a drive test."""

    def __init__(self, enabled=True):
        self.enabled = enabled and have("termux-wake-lock")
        self.held = False

    def __enter__(self):
        if self.enabled:
            try:
                run("termux-wake-lock", timeout=8.0)
                self.held = True
            except ApiError:
                self.held = False
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                run("termux-wake-unlock", timeout=8.0)
            except ApiError:
                pass
        return False


# ---------------------------------------------------------------- preflight
CHECKS = [
    ("termux-telephony-cellinfo", "serving + neighbour cells", True),
    ("termux-telephony-deviceinfo", "operator / network type", True),
    ("termux-location", "GPS logging", False),
    ("termux-wifi-scaninfo", "Wi-Fi scanner", False),
    ("termux-wifi-connectioninfo", "Wi-Fi link stats", False),
    ("termux-battery-status", "battery + temperature", False),
    ("termux-notification", "threshold alarms", False),
    ("termux-vibrate", "haptic alarms", False),
    ("termux-tts-speak", "spoken alarms", False),
    ("termux-wake-lock", "keep CPU awake while logging", False),
]


def doctor():
    """Environment report used by `nettrack doctor` and at startup."""
    rows = []
    fatal = []
    warn = []

    if in_proot():
        warn.append("Running inside proot-distro. The termux-api scripts often "
                    "still reach the Termux:API app from here, so this is only "
                    "a warning - the live probe below is what decides.")

    for cmd, purpose, required in CHECKS:
        rows.append({"cmd": cmd, "purpose": purpose, "required": required,
                     "present": have(cmd)})
        if required and not have(cmd):
            fatal.append("%s missing - run: pkg install termux-api" % cmd)

    live = {}
    if not fatal:
        _data, err = deviceinfo(timeout=12.0)
        live["deviceinfo"] = err or "ok"
        cells, cerr = cellinfo(timeout=12.0)
        if cerr:
            live["cellinfo"] = cerr
        elif not cells:
            live["cellinfo"] = ("empty list - grant Termux:API the Location "
                                "permission (Settings > Apps > Termux:API > "
                                "Permissions > Location > Allow all the time)")
        else:
            live["cellinfo"] = "ok (%d cell%s)" % (len(cells), "" if len(cells) == 1 else "s")

        if any(PLAY_STUB_MSG in str(v) for v in live.values()):
            fatal.append(
                "Termux:API is stubbed out in this build. The Google Play "
                "version of Termux does not implement telephony, Wi-Fi or\n"
                "     location, which is everything nettrack reads. "
                "Two ways forward:\n"
                "       a) `nettrack adb setup` - drive the radio through "
                "Wireless debugging instead. Nothing to reinstall,\n"
                "          keeps your files, and reports more detail "
                "(modem-declared bands, NR state).\n"
                "       b) Reinstall Termux AND Termux:API from F-Droid "
                "(f-droid.org) or github.com/termux - both from the\n"
                "          same source. Note this wipes Termux's private "
                "storage, including any proot-distro rootfs.")
    return {"rows": rows, "fatal": fatal, "warn": warn, "live": live}


class Poller(threading.Thread):
    """Calls one API function on an interval and hands results to a callback.

    Each source runs on its own thread because the tools have very different
    latencies - cellinfo returns in ~300 ms while a cold GPS fix can take 30 s.
    """

    def __init__(self, name, fn, interval, on_result, on_error=None, daemon=True):
        super().__init__(name=name, daemon=daemon)
        self.fn = fn
        self.interval = interval
        self.on_result = on_result
        self.on_error = on_error
        self._stop = threading.Event()
        self.last_ok = None
        self.last_error = None
        self.count = 0
        self.errors = 0

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                value, err = self.fn()
                if err:
                    self.errors += 1
                    self.last_error = err
                    if self.on_error:
                        self.on_error(err)
                else:
                    self.count += 1
                    self.last_ok = time.time()
                    self.last_error = None
                    self.on_result(value)
            except Exception as e:  # a collector must never kill the app
                self.errors += 1
                self.last_error = "%s: %s" % (type(e).__name__, e)
                if self.on_error:
                    self.on_error(self.last_error)
            wait = self.interval - (time.time() - t0)
            if wait > 0:
                self._stop.wait(wait)
