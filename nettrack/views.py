"""Screen renderers. Each returns a list of ready-to-print lines."""

from __future__ import annotations

import datetime as dt
import time

from . import bands, geo, model, netperf
from .ui import (QCOLOR, QNAME, badge, fit, hbar, kv, paint, rule, sparkline,
                 strip, table)

# Gauge ranges per metric: (min, max, good_threshold)
GAUGE = {
    "rsrp": (-125.0, -60.0), "rsrq": (-25.0, -3.0), "sinr": (-10.0, 30.0),
    "rssi": (-110.0, -50.0), "rscp": (-120.0, -50.0), "ecno": (-24.0, 0.0),
}


def ta_metres(cell):
    """Timing advance -> rough one-way distance."""
    if cell is None or cell.ta is None:
        return None
    if cell.tech == "lte":
        return cell.ta * 78.125
    if cell.tech == "gsm":
        return cell.ta * 553.5
    if cell.tech == "nr":
        return cell.ta * 78.125 / 2.0   # assumes 30 kHz SCS
    return None


def _fmt(v, nd=1, unit=""):
    if v is None:
        return "--"
    return ("%." + str(nd) + "f%s") % (v, unit)


def _age(ts):
    if not ts:
        return "never"
    d = time.time() - ts
    return "%.0fs" % d if d < 90 else "%.0fm" % (d / 60)


# ------------------------------------------------------------------ header
def header(engine, w):
    s = engine.snapshot()
    dev, c = s.device, s.serving
    op = (dev.operator_name if dev else None) or "no operator"
    tech = model.TECH_LABEL.get(c.tech if c else None, "--")
    nt = (dev.network_type if dev else "") or ""
    if nt and nt.lower() not in tech.lower():
        tech = "%s (%s)" % (tech, nt.upper())
    q = c.quality if c else 0
    dot = paint("●", "green" if q >= 3 else ("yellow" if q == 2 else "red"))
    left = " %s %s  %s  %s" % (paint("nettrack", "bold", "cyan"), dot,
                               paint(op, "white"), paint(tech, "purple"))
    bat = s.battery or {}
    right = "%s  %s%s" % (
        dt.datetime.now().strftime("%H:%M:%S"),
        ("%d%% " % bat["percentage"]) if bat.get("percentage") is not None else "",
        ("%.0f°C" % bat["temperature"]) if bat.get("temperature") is not None else "")
    pad = max(1, w - strip(left) - len(right) - 1)
    return left + " " * pad + paint(right, "grey")


# ------------------------------------------------------------- serving cell
def serving_block(engine, w, compact=False):
    s = engine.snapshot()
    c = s.serving
    out = [rule(w, "SERVING CELL")]
    if c is None:
        out.append(paint("  no cell reported - is Location permission granted "
                         "to Termux:API?", "red"))
        return out

    tags = [badge(model.TECH_LABEL.get(c.tech, "?"))]
    if c.band_label:
        tags.append(badge(c.describe_band(), "white", "bggrey"))
    if c.duplex:
        tags.append(paint(c.duplex, "grey"))
    if c.band_candidates and len(c.band_candidates) > 1:
        tags.append(paint("alt:" + "/".join(c.band_candidates[1:3]), "dark"))
    out.append(" " + "  ".join(tags))

    node, sector = c.node_split(int(engine.opts.get("gnb_bits", 24)))
    lw = 10
    rows = [
        ("CGI", c.cgi or "--"),
        ("PLMN", "%s   %s %s" % (c.plmn or "--",
                                 "TAC" if c.tac is not None else "LAC",
                                 c.area if c.area is not None else "--")),
        ("Cell ID", "%s%s" % (c.cid if c.cid is not None else "--",
                              "   node %s / sector %s" % (node, sector)
                              if node is not None else "")),
        ("PCI/PSC", "%s      ARFCN %s%s" % (
            c.pci if c.pci is not None else "--",
            c.arfcn if c.arfcn is not None else "--",
            "      BW %.0f MHz" % (c.bandwidth / 1000.0) if c.bandwidth else "")),
    ]
    for k, v in (rows[:2] if compact else rows):
        out.append(" " + kv(k, v, w - 1, lw))
    return out


def signal_block(engine, w, graph=True):
    s = engine.snapshot()
    c = s.serving
    out = [rule(w, "SIGNAL")]
    if c is None:
        return out
    metrics = []
    if c.tech in ("lte", "nr"):
        metrics = [("rsrp", c.rsrp, "dBm"), ("rsrq", c.rsrq, "dB"), ("sinr", c.sinr, "dB")]
    elif c.tech in ("wcdma", "tdscdma"):
        metrics = [("rscp", c.rscp, "dBm"), ("ecno", c.ecno, "dB")]
    else:
        metrics = [("rssi", c.rssi, "dBm")]

    labw, valw = 6, 12
    barw = max(8, w - labw - valw - 14)
    for i, (name, val, unit) in enumerate(metrics):
        lo, hi = GAUGE.get(name, (-120.0, -40.0))
        col = QCOLOR[c.quality] if i == 0 else ("cyan" if val is not None else "dark")
        line = " %s %s %s" % (
            paint(name.upper().ljust(labw - 1), "grey"),
            paint(_fmt(val, 1, " " + unit).rjust(valw), col if i == 0 else "white"),
            hbar(val, lo, hi, barw, col if isinstance(col, str) else None))
        if i == 0:
            line += " " + paint(QNAME[c.quality], col)
        out.append(line)
        if graph and i == 0:
            hist = list(engine.hist.get(name, []))
            out.append(" " * (labw + 1) + paint("hist", "dark") + " " +
                       sparkline(hist, max(10, w - labw - 8), col, lo, hi))

    extras = []
    if c.cqi is not None:
        extras.append("CQI %d" % c.cqi)
    if c.ta is not None:
        d = ta_metres(c)
        extras.append("TA %d%s" % (c.ta, " (~%s)" % geo.fmt_dist(d) if d else ""))
    if c.asu is not None:
        extras.append("ASU %d" % c.asu)
    if c.level is not None:
        extras.append("bars %d/4" % c.level)
    if extras:
        out.append(" " + paint("   ".join(extras), "grey"))
    return out


# -------------------------------------------------------------- neighbours
def neighbour_rows(engine, limit=None):
    s = engine.snapshot()
    srv = s.serving
    base = srv.primary if srv else None
    rows = []
    for n in (s.neighbours or [])[:limit]:
        delta = ""
        if base is not None and n.primary is not None:
            d = n.primary - base
            delta = paint("%+.1f" % d, "green" if d > -6 else
                          ("yellow" if d > -12 else "dark"))
        q = n.quality
        rows.append([
            paint(model.TECH_LABEL.get(n.tech, "?"), "purple"),
            n.pci if n.pci is not None else "--",
            n.arfcn if n.arfcn is not None else "--",
            n.band_label or "--",
            paint(_fmt(n.primary, 1), QCOLOR[q]),
            _fmt(n.rsrq, 1) if n.rsrq is not None else "--",
            delta,
        ])
    return rows


def neighbours_block(engine, w, limit=6):
    s = engine.snapshot()
    n = len(s.neighbours or [])
    out = [rule(w, "NEIGHBOURS (%d)" % n)]
    if not n:
        out.append(paint("  none reported", "dark"))
        return out
    widths = _nb_widths(w)
    out += [" " + l for l in table(["TECH", "PCI", "ARFCN", "BAND", "RSRP", "RSRQ", "Δ"],
                                   neighbour_rows(engine, limit), widths,
                                   ["<", ">", ">", "<", ">", ">", ">"])]
    return out


def _nb_widths(w):
    base = [6, 5, 6, 7, 7, 6, 6]
    total = sum(base) + len(base) - 1
    if total > w - 1:
        base = [5, 4, 6, 5, 7, 6, 0]
    return base


# ---------------------------------------------------------------- location
def location_block(engine, w):
    s = engine.snapshot()
    f = s.fix
    out = [rule(w, "LOCATION & SITE")]
    if f is None:
        out.append(paint("  no GPS fix yet", "dark"))
    else:
        out.append(" " + kv("Position", "%.6f, %.6f" % (f.lat, f.lon), w - 1, 10))
        bits = ["acc %s" % geo.fmt_dist(f.accuracy),
                "%.1f km/h" % (f.kmh or 0.0),
                "hdg %s" % (geo.compass(f.bearing))]
        if f.alt is not None:
            bits.append("alt %.0f m" % f.alt)
        bits.append("odo %s" % geo.fmt_dist(s.odometer or 0))
        out.append(" " + paint("   ".join(bits), "grey"))
    if s.site is not None:
        line = "%s" % (s.site.name or "site")
        if s.distance_m is not None:
            line += "   %s %s" % (geo.fmt_dist(s.distance_m),
                                  geo.compass(s.site_bearing))
        if s.off_boresight is not None:
            line += "   off-boresight %+.0f°" % s.off_boresight
        out.append(" " + kv("Site", line, w - 1, 10))
    elif len(engine.cellfile):
        out.append(" " + paint("Site       not in cellfile", "dark"))
    return out


# -------------------------------------------------------------- throughput
def perf_block(engine, w):
    s = engine.snapshot()
    out = [rule(w, "THROUGHPUT & LATENCY")]
    dl = netperf.fmt_mbps(s.dl_kbps / 1000.0) if s.dl_kbps else "--"
    ul = netperf.fmt_mbps(s.ul_kbps / 1000.0) if s.ul_kbps else "--"
    png = "%.0f ms" % s.ping_ms if s.ping_ms else "--"
    out.append(" %s %s   %s %s   %s %s" % (
        paint("DL", "grey"), paint(dl, "green"),
        paint("UL", "grey"), paint(ul, "cyan"),
        paint("RTT", "grey"), paint(png, "yellow")))
    out.append(" " + paint("press s to run a speed test (uses mobile data)", "dark"))
    return out


# ------------------------------------------------------------------ events
def events_block(engine, w, limit=4):
    out = [rule(w, "EVENTS")]
    evs = list(engine.events)[-limit:]
    if not evs:
        out.append(paint("  none yet", "dark"))
    kind_col = {"ho": "yellow", "tech": "purple", "band": "cyan",
                "loss": "red", "alarm": "red", "camp": "green"}
    for e in evs:
        out.append(" %s %s %s" % (
            paint(dt.datetime.fromtimestamp(e.ts).strftime("%H:%M:%S"), "dark"),
            paint(e.kind.upper().ljust(5), kind_col.get(e.kind, "grey")),
            fit(e.text, max(0, w - 17))))
    return out


# ------------------------------------------------------------------ footer
def footer(engine, w, keys):
    h = engine.health()
    bad = [n for n, v in h.items() if v["err"] and not v["ok"]]
    st = paint("  ".join("%s:%s" % (n[0].upper(), _age(v["last"])) for n, v in h.items()),
               "red" if bad else "dark")
    left = " " + "  ".join(keys)
    pad = max(1, w - strip(left) - strip(st) - 1)
    return left + " " * pad + st


def status_errors(engine, w):
    with engine.lock:
        msgs = list(engine.status.items())
    return [paint(" ! %s: %s" % (k, fit(v, w - len(k) - 6)), "red") for k, v in msgs[:2]]


# ------------------------------------------------------------- full screens
KEYS_MAIN = ["[1]dash", "[2]cells", "[3]wifi", "[4]events", "[s]peed", "[q]uit"]


def dashboard(engine, w, h):
    lines = [header(engine, w)]
    lines += serving_block(engine, w)
    lines += signal_block(engine, w)
    budget = h - len(lines) - 1
    blocks = [neighbours_block(engine, w, limit=6), location_block(engine, w),
              perf_block(engine, w), events_block(engine, w, limit=4)]
    for b in blocks:
        if budget - len(b) < 1:
            break
        lines += b
        budget -= len(b)
    lines += status_errors(engine, w)
    while len(lines) < h - 1:
        lines.append("")
    lines = lines[:h - 1]
    lines.append(footer(engine, w, KEYS_MAIN))
    return lines


def cells_screen(engine, w, h):
    lines = [header(engine, w)]
    lines += serving_block(engine, w, compact=True)
    lines += signal_block(engine, w, graph=False)
    lines.append(rule(w, "ALL DETECTED CELLS"))
    widths = _nb_widths(w)
    rows = neighbour_rows(engine, limit=max(1, h - len(lines) - 3))
    lines += [" " + l for l in table(["TECH", "PCI", "ARFCN", "BAND", "RSRP", "RSRQ", "Δ"],
                                     rows, widths, ["<", ">", ">", "<", ">", ">", ">"])]
    while len(lines) < h - 1:
        lines.append("")
    lines = lines[:h - 1]
    lines.append(footer(engine, w, KEYS_MAIN))
    return lines


def wifi_screen(engine, w, h, wifi_state):
    lines = [header(engine, w)]
    conn = wifi_state.get("conn") or {}
    lines.append(rule(w, "WI-FI LINK"))
    if conn.get("ssid"):
        band, ch = bands.wifi_channel(conn.get("frequency_mhz"))
        lines.append(" " + kv("SSID", conn.get("ssid"), w - 1, 10))
        lines.append(" " + kv("BSSID", "%s   %s ch%s" % (conn.get("bssid", "--"), band, ch),
                              w - 1, 10))
        rssi = conn.get("rssi")
        lines.append(" " + kv("RSSI", "%s dBm   %s Mbps   %s" % (
            rssi, conn.get("link_speed_mbps", "--"), conn.get("ip", "")), w - 1, 10))
        lines.append(" " + hbar(rssi, -95, -35, max(8, w - 4), "green"))
    else:
        lines.append(paint("  not associated", "dark"))

    aps = wifi_state.get("scan") or []
    lines.append(rule(w, "SCAN (%d AP)" % len(aps)))
    rows = []
    for ap in sorted(aps, key=lambda a: a.get("rssi", -999), reverse=True):
        band, ch = bands.wifi_channel(ap.get("frequency_mhz"))
        r = ap.get("rssi")
        q = 4 if r >= -50 else 3 if r >= -60 else 2 if r >= -70 else 1 if r >= -80 else 0
        col = ("green", "green2", "yellow", "orange", "red")[4 - q]
        rows.append([fit(ap.get("ssid") or "<hidden>", 16), band or "--", ch or "--",
                     paint(str(r), col), ap.get("channel_bandwidth_mhz", "--"),
                     hbar(r, -95, -35, 10, col)])
    avail = max(1, h - len(lines) - 2)
    lines += [" " + l for l in table(["SSID", "BAND", "CH", "RSSI", "BW", ""],
                                     rows[:avail], [16, 7, 4, 5, 4, 10],
                                     ["<", "<", ">", ">", ">", "<"])]
    if wifi_state.get("error"):
        lines.append(paint(" ! " + wifi_state["error"], "red"))
    while len(lines) < h - 1:
        lines.append("")
    lines = lines[:h - 1]
    lines.append(footer(engine, w, ["[r]escan"] + KEYS_MAIN))
    return lines


def events_screen(engine, w, h):
    lines = [header(engine, w), rule(w, "EVENT LOG")]
    evs = list(engine.events)[-(h - 4):]
    kind_col = {"ho": "yellow", "tech": "purple", "band": "cyan",
                "loss": "red", "alarm": "red", "camp": "green"}
    for e in evs:
        lines.append(" %s %s %s" % (
            paint(dt.datetime.fromtimestamp(e.ts).strftime("%H:%M:%S"), "dark"),
            paint(e.kind.upper().ljust(5), kind_col.get(e.kind, "grey")),
            fit(e.text, max(0, w - 17))))
    if not evs:
        lines.append(paint("  no events recorded yet", "dark"))
    while len(lines) < h - 1:
        lines.append("")
    lines = lines[:h - 1]
    lines.append(footer(engine, w, KEYS_MAIN))
    return lines


def help_screen(w, h):
    body = [
        "", paint("  nettrack - cellular network monitor for Termux", "bold", "cyan"), "",
        "  KEYS",
        "    1 / d      dashboard",
        "    2 / c      all detected cells",
        "    3 / w      Wi-Fi link and scanner",
        "    4 / e      event log (handovers, band changes, alarms)",
        "    s          run a download/upload speed test",
        "    p          run a latency test",
        "    L          start/stop CSV logging",
        "    r          rescan (Wi-Fi screen)",
        "    g          toggle GPS collector",
        "    h / ?      this help",
        "    q          quit",
        "",
        "  NOTES",
        "    Signal metrics come from termux-telephony-cellinfo, so the modem",
        "    decides how often they refresh - roughly once per second.",
        "    Android does not expose device-wide byte counters to a shell app,",
        "    so throughput is measured by generating traffic (it uses data).",
        "",
    ]
    lines = [fit(l, w) for l in body]
    while len(lines) < h - 1:
        lines.append("")
    lines = lines[:h - 1]
    lines.append(paint(" press any key to return", "dark"))
    return lines
