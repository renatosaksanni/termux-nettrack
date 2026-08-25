"""ADB-over-localhost data source.

Android exposes far more radio detail to a `shell` uid than to an app, and
Wireless Debugging (Android 11+) lets the phone hand itself that uid without
root and without a PC. This module drives `adb` and reshapes what `dumpsys`
prints into the same dict shape termux-api produces, so the rest of nettrack
does not care which backend supplied a sample.

The dumpsys text is Java toString() output, not a stable API. Parsing is
therefore deliberately loose: find the blocks, harvest every key=value pair,
and let missing fields stay missing rather than guessing.
"""

from __future__ import annotations

import re
import socket
import shutil
import subprocess

DEFAULT_HOST = "127.0.0.1"


class AdbError(Exception):
    pass


# --------------------------------------------------------------- primitives
def _adb_path():
    return shutil.which("adb") or "adb"


def adb(args, timeout=15.0, serial=None):
    """Run an adb command; returns (stdout, stderr, returncode)."""
    argv = [_adb_path()]
    if serial:
        argv += ["-s", serial]
    argv += [str(a) for a in args]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise AdbError("adb not found - run: pkg install android-tools")
    except subprocess.TimeoutExpired:
        raise AdbError("adb %s timed out" % (args[0] if args else ""))
    except OSError as e:
        raise AdbError("adb failed: %s" % e)
    return (p.stdout, p.stderr, p.returncode)


def devices():
    """Connected devices as [(serial, state)]."""
    out, _err, _rc = adb(["devices"], timeout=10.0)
    rows = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


PAIRING_SVC = "_adb-tls-pairing._tcp"
CONNECT_SVC = "_adb-tls-connect._tcp"


def mdns_services(timeout=20.0):
    """Wireless debugging endpoints, as advertised over mDNS.

    Android picks a fresh random port every time the dialog opens, and the
    pairing port differs from the connect port. Asking adb to discover them
    removes the step where those two get mixed up.
    """
    try:
        adb(["start-server"], timeout=timeout)
    except AdbError:
        pass
    try:
        out, err, _rc = adb(["mdns", "services"], timeout=timeout)
    except AdbError:
        return []
    found = []
    for line in (out + "\n" + err).splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].startswith("_adb"):
            addr = parts[2]
            host, _sep, port = addr.rpartition(":")
            if port.isdigit():
                found.append({"name": parts[0], "type": parts[1],
                              "host": host or DEFAULT_HOST, "port": int(port)})
    return found


def local_ip():
    """This device's LAN address, without needing any permission."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# Android picks the wireless-debugging port at random; in practice it lands in
# the ephemeral range. Scanning is the fallback when adb has no mDNS backend
# compiled in, which is the case for the Termux build.
SCAN_RANGE = (35000, 45000)


def scan_ports(host, lo=None, hi=None, workers=800, timeout=0.08):
    """Open TCP ports on `host` within the wireless-debugging range."""
    import concurrent.futures as cf
    lo = lo or SCAN_RANGE[0]
    hi = hi or SCAN_RANGE[1]

    def probe(port):
        c = socket.socket()
        c.settimeout(timeout)
        try:
            c.connect((host, port))
            return port
        except OSError:
            return None
        finally:
            c.close()

    out = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(probe, range(lo, hi)):
            if r:
                out.append(r)
    return out


# Android publishes the wireless-debugging port as a system property. Reading
# it is exact and instant, unlike scanning, and unlike mDNS it works with the
# Termux adb build, which has no discovery backend compiled in.
PORT_PROPS = ("service.adb.tls.port", "persist.adb.tls_server.port")


def getprop(name, timeout=6.0):
    try:
        p = subprocess.run(["getprop", name], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    v = (p.stdout or "").strip()
    return v or None


def port_from_props():
    for prop in PORT_PROPS:
        v = getprop(prop)
        if v and v.isdigit() and int(v) > 0:
            return int(v)
    return None


def candidate_endpoints(progress=None, scan=True):
    """Plausible adb endpoints, cheapest discovery method first."""
    found = []
    port = port_from_props()
    if port:
        for host in [h for h in (local_ip(), DEFAULT_HOST) if h]:
            found.append((host, port, "getprop"))
        return found
    for sv in mdns_services():
        found.append((sv["host"], sv["port"], "mdns"))
    if found or not scan:
        return found
    for host in [h for h in (local_ip(), DEFAULT_HOST) if h]:
        if progress:
            progress(host)
        for p_ in scan_ports(host):
            found.append((host, p_, "scan"))
        if found:
            break
    return found


def find_endpoint(kind, timeout=20.0):
    """One mDNS endpoint of the given service type, or None."""
    for svc in mdns_services(timeout):
        if svc["type"] == kind:
            return svc
    return None


def connect(host=DEFAULT_HOST, port=None, timeout=15.0):
    target = "%s:%s" % (host, port) if port else host
    out, err, _rc = adb(["connect", target], timeout=timeout)
    text = (out + err).strip()
    ok = "connected to" in text.lower() and "cannot" not in text.lower()
    return (ok, text)


# ------------------------------------------------------------------ parsing
_NUM = r"-?\d+(?:\.\d+)?"


def _kv_pairs(text):
    """Every `key=value` / `key = value` pair in a block, keys normalised.

    Leading `m` (the AOSP member prefix) is stripped and case folded, so
    mCi/ci, mNrArfcn/nrArfcn all collapse to one spelling.
    """
    out = {}
    for m in re.finditer(r"\b(m?[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                         r"(\[[^\]]*\]|\{[^{}]*\}|[^\s,}\]]+)", text):
        key, val = m.group(1), m.group(2).strip()
        k = key[1:] if (len(key) > 1 and key[0] == "m" and key[1].isupper()) else key
        k = k.lower()
        if k not in out:
            out[k] = val
    return out


# Android signals "unavailable" with the int/long extremes. Only those exact
# values are sentinels - an NR NCI is 36 bits and legitimately exceeds 2^31.
SENTINELS = frozenset((2147483647, -2147483648,
                       9223372036854775807, -9223372036854775808))


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    n = int(f) if f.is_integer() else f
    if n in SENTINELS:
        return None
    return n


def _bands(v):
    """`mBands=[3]` -> [3]. The modem's own answer beats deriving from ARFCN."""
    if not v:
        return []
    nums = re.findall(r"-?\d+", v)
    return [int(n) for n in nums]


def _split_blocks(text, kinds):
    """Yield (kind, block_text) for each top-level CellInfoXxx:{...}."""
    pat = re.compile(r"CellInfo(%s)\s*[:=]?\s*\{" % "|".join(kinds))
    for m in pat.finditer(text):
        start = m.end() - 1           # at the opening brace
        depth, i = 0, start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield (m.group(1).lower(), text[start:i + 1])


_KINDS = ("Lte", "Nr", "Gsm", "Wcdma", "Tdscdma", "Cdma")


def _normalise_sinr(v):
    """RSSNR arrives either in dB or in 0.1 dB depending on the RIL.

    Real LTE SINR lives roughly in -20..+40 dB, so a magnitude far outside
    that is the tenths encoding.
    """
    if v is None:
        return None
    return v / 10.0 if abs(v) > 45 else v


def parse_cellinfo(text):
    """`dumpsys telephony.registry` -> termux-api shaped cell dicts."""
    cells = []
    for tech, block in _split_blocks(text, _KINDS):
        kv = _kv_pairs(block)
        reg = str(kv.get("registered", "")).upper()
        d = {
            "type": {"lte": "lte", "nr": "nr", "gsm": "gsm", "wcdma": "wcdma",
                     "tdscdma": "tdscdma", "cdma": "cdma"}[tech],
            "registered": reg in ("YES", "TRUE", "1"),
        }
        # identity
        for src, dst in (("ci", "ci"), ("cid", "cid"), ("nci", "nci"),
                         ("pci", "pci"), ("psc", "psc"), ("bsic", "bsic"),
                         ("tac", "tac"), ("lac", "lac"),
                         ("mcc", "mcc"), ("mnc", "mnc"),
                         ("earfcn", "earfcn"), ("nrarfcn", "nrarfcn"),
                         ("uarfcn", "uarfcn"), ("arfcn", "arfcn"),
                         ("bandwidth", "bandwidth")):
            v = _num(kv.get(src))
            if v is not None:
                d[dst] = v
        # signal
        for src, dst in (("rsrp", "rsrp"), ("rsrq", "rsrq"), ("cqi", "cqi"),
                         ("rssi", "rssi"), ("ta", "timing_advance"),
                         ("level", "level"), ("rscp", "rscp"), ("ecno", "ecno"),
                         ("ssrsrp", "ss_rsrp"), ("ssrsrq", "ss_rsrq"),
                         ("sssinr", "ss_sinr"), ("csirsrp", "csi_rsrp"),
                         ("csirsrq", "csi_rsrq"), ("csisinr", "csi_sinr"),
                         ("ss", "rssi")):
            v = _num(kv.get(src))
            if v is not None and dst not in d:
                d[dst] = v
        sn = _normalise_sinr(_num(kv.get("rssnr")))
        if sn is not None:
            d["rssnr"] = sn
        bands = _bands(kv.get("bands"))
        if bands:
            d["bands"] = bands
        if kv.get("alphalong") and kv["alphalong"] not in ("null", "{}"):
            d["operator"] = kv["alphalong"]
        cells.append(d)
    return cells


def parse_getprop(text):
    """`getprop` bulk output -> {name: value}."""
    props = {}
    for m in re.finditer(r"^\[([^\]]+)\]:\s*\[([^\]]*)\]\s*$", text, re.M):
        props[m.group(1)] = m.group(2)
    return props


def deviceinfo_from_props(props):
    """Build a termux-telephony-deviceinfo shaped dict from system props."""
    def g(*names):
        for n in names:
            v = props.get(n)
            if v not in (None, "", "unknown"):
                return v
        return None

    net = g("gsm.network.type", "gsm.network.type.0")
    if net and "," in net:
        net = net.split(",")[0]
    return {
        "network_operator_name": g("gsm.operator.alpha", "gsm.sim.operator.alpha"),
        "network_operator": g("gsm.operator.numeric", "gsm.sim.operator.numeric"),
        "network_country_iso": g("gsm.operator.iso-country", "gsm.sim.operator.iso-country"),
        "network_type": (net or "").lower() or None,
        "network_roaming": str(g("gsm.operator.isroaming") or "false").lower() == "true",
        "sim_state": (g("gsm.sim.state") or "").lower() or None,
        "sim_operator_name": g("gsm.sim.operator.alpha"),
        "sim_operator": g("gsm.sim.operator.numeric"),
        "phone_type": "gsm",
        "data_state": "connected" if g("gsm.defaultpdpcontext.active") == "true" else None,
    }


_LOC_RE = re.compile(
    r"Location\[\s*(?P<prov>\w+)\s+(?P<lat>-?\d+\.\d+),(?P<lon>-?\d+\.\d+)"
    r"(?P<rest>[^\]]*)\]")


def parse_location(text):
    """Pull the freshest last-known fix out of `dumpsys location`."""
    best = None
    for m in _LOC_RE.finditer(text):
        rest = m.group("rest")

        def f(key):
            mm = re.search(key + r"=(-?\d+(?:\.\d+)?)", rest)
            return float(mm.group(1)) if mm else None

        age = None
        am = re.search(r"\bet=([^\s\]]+)", rest)
        if am:
            age = _duration_ms(am.group(1))
        fix = {"provider": m.group("prov"),
               "latitude": float(m.group("lat")), "longitude": float(m.group("lon")),
               "accuracy": f("hAcc"), "altitude": f("alt"),
               "speed": f("vel"), "bearing": f("bear"),
               "elapsedMs": age}
        rank = {"gps": 0, "fused": 1, "network": 2, "passive": 3}.get(m.group("prov"), 9)
        if best is None or rank < best[0]:
            best = (rank, fix)
    return best[1] if best else None


def _duration_ms(s):
    """`+1d2h3m4s5ms` -> milliseconds."""
    total = 0.0
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|[dhms])", s):
        total += float(val) * {"d": 86400000, "h": 3600000, "m": 60000,
                               "s": 1000, "ms": 1}[unit]
    return total or None


def parse_wifi_status(text):
    """`cmd wifi status` -> termux-wifi-connectioninfo shape."""
    if "not connected" in text.lower():
        return {}
    out = {}
    m = re.search(r'SSID:\s*"?([^",]+)"?', text)
    if m:
        out["ssid"] = m.group(1).strip()
    m = re.search(r"BSSID:\s*([0-9a-fA-F:]{17})", text)
    if m:
        out["bssid"] = m.group(1)
    m = re.search(r"RSSI:\s*(-?\d+)", text)
    if m:
        out["rssi"] = int(m.group(1))
    m = re.search(r"Frequency:\s*(\d+)", text)
    if m:
        out["frequency_mhz"] = int(m.group(1))
    m = re.search(r"(?:Tx Link speed|Link speed):\s*(\d+)", text)
    if m:
        out["link_speed_mbps"] = int(m.group(1))
    m = re.search(r"IP:\s*([0-9.]+)", text)
    if m:
        out["ip"] = m.group(1)
    return out


def parse_scan_results(text):
    """`cmd wifi list-scan-results` -> termux-wifi-scaninfo shape."""
    aps = []
    for line in text.splitlines():
        m = re.match(r"\s*([0-9a-fA-F:]{17})\s+(\d+)\s+(-?\d+)\s+(\S+)\s*(.*)", line)
        if not m:
            continue
        rest = m.group(5).strip()
        ssid = rest.split("[")[0].strip() or None
        aps.append({"bssid": m.group(1), "frequency_mhz": int(m.group(2)),
                    "rssi": int(m.group(3)), "ssid": ssid})
    return aps


def parse_battery(text):
    """`dumpsys battery` -> termux-battery-status shape."""
    kv = {}
    for line in text.splitlines():
        if ":" in line:
            k, _s, v = line.partition(":")
            kv[k.strip().lower()] = v.strip()
    out = {}
    if "level" in kv:
        try:
            out["percentage"] = int(kv["level"])
            out["level"] = out["percentage"]
        except ValueError:
            pass
    if "temperature" in kv:
        try:
            out["temperature"] = int(kv["temperature"]) / 10.0
        except ValueError:
            pass
    st = kv.get("status")
    out["status"] = {"2": "CHARGING", "3": "DISCHARGING", "5": "FULL"}.get(st, st)
    return out


# ------------------------------------------------------------------- source
class AdbSource:
    """Reads the radio through `adb shell`, exposing the api module's surface.

    Battery still comes from termux-api when available: that one endpoint
    works even in the stubbed Google Play build, and it is cheaper than
    spawning adb.
    """

    name = "adb"

    def __init__(self, serial=None, host=DEFAULT_HOST, port=None):
        self.serial = serial
        self.host = host
        self.port = port
        self.last_error = None

    # -- plumbing -------------------------------------------------------
    def target(self):
        if self.serial:
            return self.serial
        if self.port:
            return "%s:%s" % (self.host, self.port)
        return None

    def shell(self, cmd, timeout=15.0):
        out, err, rc = adb(["shell", cmd], timeout=timeout, serial=self.target())
        if rc != 0:
            msg = (err or out).strip().splitlines()
            raise AdbError(msg[0] if msg else "adb shell exited %d" % rc)
        low = (out or "").lower()
        if "device unauthorized" in low or "no devices/emulators found" in low:
            raise AdbError("adb not authorised - run `nettrack adb setup`")
        return out

    def _try(self, fn, default):
        try:
            return (fn(), None)
        except AdbError as e:
            self.last_error = str(e)
            return (default, str(e))
        except Exception as e:                      # a parser must not crash a poller
            self.last_error = "%s: %s" % (type(e).__name__, e)
            return (default, self.last_error)

    def status(self):
        """(connected, detail) for doctor and the adb subcommand."""
        try:
            rows = devices()
        except AdbError as e:
            return (False, str(e))
        live = [(s, st) for s, st in rows if st == "device"]
        if not live:
            if rows:
                return (False, "adb sees %s but none are ready" %
                        ", ".join("%s (%s)" % r for r in rows))
            return (False, "no adb device - enable Wireless debugging and pair")
        return (True, ", ".join(s for s, _ in live))

    # -- api surface ----------------------------------------------------
    def cellinfo(self, timeout=15.0):
        def go():
            text = self.shell("dumpsys telephony.registry", timeout=timeout)
            cells = parse_cellinfo(text)
            if not cells:
                raise AdbError("dumpsys telephony.registry reported no cells "
                               "(grant Location to the shell? try `adb shell "
                               "dumpsys telephony.registry | grep mCellInfo`)")
            return cells
        return self._try(go, [])

    def deviceinfo(self, timeout=15.0):
        return self._try(
            lambda: deviceinfo_from_props(parse_getprop(self.shell("getprop", timeout=timeout))),
            {})

    def location(self, provider="gps", request="last", timeout=20.0):
        def go():
            fix = parse_location(self.shell("dumpsys location", timeout=timeout))
            if fix is None:
                raise AdbError("no last-known location in dumpsys location")
            return fix
        return self._try(go, None)

    def battery(self, timeout=15.0):
        from . import api
        if api.have("termux-battery-status"):
            data, err = api.battery(timeout=min(timeout, 10.0))
            if not err and isinstance(data, dict) and data:
                return (data, None)
        return self._try(lambda: parse_battery(self.shell("dumpsys battery", timeout=timeout)), {})

    def wifi_connection(self, timeout=15.0):
        return self._try(lambda: parse_wifi_status(self.shell("cmd wifi status", timeout=timeout)), {})

    def wifi_scan(self, timeout=25.0):
        return self._try(
            lambda: parse_scan_results(self.shell("cmd wifi list-scan-results", timeout=timeout)),
            [])


SETUP_GUIDE = """\
Wireless debugging gives this phone a `shell` uid over loopback - no root,
no PC. It survives until reboot; the pairing itself is permanent.

  1. Settings > About phone > Software information
     tap "Build number" seven times to unlock Developer options.

  2. Settings > Developer options > Wireless debugging  ->  ON
     (keep this screen open, the ports change when you leave it)

  3. Tap "Pair device with pairing code". Leave that dialog open - closing it
     invalidates both the port and the code - and in Termux type just the
     6-digit code; the port is discovered for you:

         nettrack adb pair <CODE>

  4. Close the dialog, then:

         nettrack adb connect

  5. Check it:

         nettrack adb status
         nettrack doctor --source adb

After a reboot, repeat only step 4 - the pairing is remembered. Same after
anything that drops the connection, such as toggling Auto Blocker or Wireless
debugging.

If it does not connect:

  * Samsung Auto Blocker blocks adb outright, and it must stay OFF for as
    long as you use this backend: Settings > Security and privacy > Auto
    Blocker. Switching it back on drops the live connection, so nettrack
    stops working until you turn it off and run `nettrack adb connect` again.
  * Wi-Fi has to be ON. Android greys out Wireless debugging on mobile data
    alone, and adbd binds to the Wi-Fi interface, not loopback - so 127.0.0.1
    is often refused. `nettrack adb discover` finds the right address.
  * If 127.0.0.1 is refused, use the address printed on the Wireless
    debugging screen itself:

         nettrack adb connect <PORT> --adb-host 192.168.x.x

  * The pairing dialog and the main screen show DIFFERENT ports. Pair with
    the first, connect with the second.
  * `adb devices` should list one entry in state `device`. `unauthorized`
    means the pairing did not complete - pair again."""
