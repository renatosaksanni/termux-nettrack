"""Session logging: CSV, raw JSONL and KML export."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import xml.sax.saxutils as sx

# Column names follow G-NetTrack's log layout where an equivalent exists, so
# existing post-processing scripts mostly work unchanged; the extra columns
# are appended at the end.
COLUMNS = [
    "Timestamp", "Longitude", "Latitude", "Altitude", "Accuracy", "Speed", "Heading",
    "Distance", "Operatorname", "MCC", "MNC", "CGI", "Networktech", "Networkmode",
    "Band", "Frequency", "ARFCN", "Bandwidth", "LAC", "TAC", "CellID", "Node",
    "Sector", "PSC", "RSRP", "RSRQ", "SNR", "CQI", "RSSI", "RSCP", "ECNO", "TA",
    "Level", "ASU", "DL_bitrate", "UL_bitrate", "PingRTT", "SiteName",
    "SiteDistance", "SiteBearing", "OffBoresight", "NeighborCount", "Neighbors",
    "BatteryLevel", "BatteryTemp", "Event",
]


def default_log_dir():
    """Prefer shared storage so logs are reachable from the file manager."""
    home = os.path.expanduser("~")
    shared = os.path.join(home, "storage", "shared")
    base = shared if os.path.isdir(shared) else home
    return os.path.join(base, "nettrack")


def _r(v, nd=1):
    if v is None:
        return ""
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return v


def row_from_sample(s, event=""):
    """Flatten a Sample into the CSV column order."""
    c = s.serving
    fix = s.fix
    node, sector = (c.node_split() if c else (None, None))
    nb = ";".join("%s:%s" % (n.pci if n.pci is not None else "?",
                             int(n.primary) if n.primary is not None else "")
                  for n in (s.neighbours or [])[:8])
    dev = s.device
    return [
        dt.datetime.fromtimestamp(s.ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        _r(fix.lon if fix else None, 7), _r(fix.lat if fix else None, 7),
        _r(fix.alt if fix else None, 1), _r(fix.accuracy if fix else None, 1),
        _r(fix.kmh if fix else None, 1), _r(fix.bearing if fix else None, 1),
        _r(s.odometer, 1),
        (dev.operator_name if dev else "") or "",
        (c.mcc if c else "") or "", (c.mnc if c else "") or "",
        (c.cgi if c else "") or "",
        (c.tech.upper() if c and c.tech else ""),
        (dev.network_type if dev else "") or "",
        (c.band_label if c else "") or "", _r(c.freq_mhz if c else None, 2),
        (c.arfcn if c else "") or "", (c.bandwidth if c else "") or "",
        (c.lac if c else "") or "", (c.tac if c else "") or "",
        (c.cid if c else "") or "", node if node is not None else "",
        sector if sector is not None else "", (c.pci if c else "") or "",
        _r(c.rsrp if c else None), _r(c.rsrq if c else None),
        _r(c.sinr if c else None), _r(c.cqi if c else None),
        _r(c.rssi if c else None), _r(c.rscp if c else None),
        _r(c.ecno if c else None), _r(c.ta if c else None),
        _r(c.level if c else None), _r(c.asu if c else None),
        _r(s.dl_kbps, 1), _r(s.ul_kbps, 1), _r(s.ping_ms, 1),
        (s.site.name if s.site else "") or "",
        _r(s.distance_m, 1), _r(s.site_bearing, 1), _r(s.off_boresight, 1),
        len(s.neighbours or []), nb,
        _r((s.battery or {}).get("percentage"), 0),
        _r((s.battery or {}).get("temperature"), 1),
        event or (s.note or ""),
    ]


class SessionWriter:
    """Owns one measurement session on disk."""

    def __init__(self, directory=None, name=None, raw=False):
        self.dir = directory or default_log_dir()
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.name = name or ("nettrack-%s" % stamp)
        os.makedirs(self.dir, exist_ok=True)
        self.csv_path = os.path.join(self.dir, self.name + ".csv")
        self.raw_path = os.path.join(self.dir, self.name + ".jsonl") if raw else None
        self.events_path = os.path.join(self.dir, self.name + ".events.log")
        self._fh = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(COLUMNS)
        self._fh.flush()
        self._raw = open(self.raw_path, "w", encoding="utf-8") if raw else None
        self._ev = None
        self.rows = 0

    def write(self, sample, event=""):
        self._w.writerow(row_from_sample(sample, event))
        self.rows += 1
        if self.rows % 10 == 0:
            self._fh.flush()
        if self._raw is not None:
            try:
                self._raw.write(json.dumps({
                    "ts": sample.ts,
                    "cells": [c.raw for c in ([sample.serving] if sample.serving else [])
                              + list(sample.neighbours or []) if c.raw is not None],
                    "device": sample.device.raw if sample.device else None,
                    "fix": {k: getattr(sample.fix, k) for k in
                            ("lat", "lon", "alt", "accuracy", "speed", "bearing", "provider")}
                    if sample.fix else None,
                }, default=str) + "\n")
            except (TypeError, ValueError):
                pass

    def event(self, text):
        if self._ev is None:
            self._ev = open(self.events_path, "a", encoding="utf-8")
        self._ev.write("%s  %s\n" % (dt.datetime.now().strftime("%H:%M:%S"), text))
        self._ev.flush()

    def close(self):
        for fh in (self._fh, self._raw, self._ev):
            if fh:
                try:
                    fh.flush()
                    fh.close()
                except OSError:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ------------------------------------------------------------------- KML
# RSRP buckets -> KML AABBGGRR colours (opaque).
_KML_BUCKETS = [
    (-80, "ff00c800", "Excellent (>= -80)"),
    (-90, "ff00e6e6", "Good (-80..-90)"),
    (-100, "ff00a5ff", "Fair (-90..-100)"),
    (-110, "ff0000ff", "Poor (-100..-110)"),
    (-999, "ff400080", "Bad (< -110)"),
]


def _bucket(v):
    if v is None:
        return 4
    for i, (th, _c, _l) in enumerate(_KML_BUCKETS):
        if v >= th:
            return i
    return len(_KML_BUCKETS) - 1


def csv_to_kml(csv_path, kml_path=None, metric="RSRP"):
    """Turn a session CSV into a colour-coded KML track for Google Earth."""
    kml_path = kml_path or os.path.splitext(csv_path)[0] + ".kml"
    pts = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            try:
                lat, lon = float(r["Latitude"]), float(r["Longitude"])
            except (TypeError, ValueError, KeyError):
                continue
            if lat == 0 and lon == 0:
                continue
            try:
                val = float(r.get(metric) or "")
            except ValueError:
                val = None
            pts.append((lat, lon, val, r))
    if not pts:
        raise ValueError("no rows with coordinates in %s" % csv_path)

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        "<name>%s</name>" % sx.escape(os.path.basename(csv_path)),
    ]
    for i, (_th, color, label) in enumerate(_KML_BUCKETS):
        out.append(
            '<Style id="b%d"><IconStyle><color>%s</color><scale>0.6</scale>'
            '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href>'
            "</Icon></IconStyle></Style>" % (i, color))
    out.append('<Style id="path"><LineStyle><color>ffffffff</color><width>2</width></LineStyle></Style>')

    out.append('<Placemark><name>Track</name><styleUrl>#path</styleUrl><LineString>'
               "<tessellate>1</tessellate><coordinates>")
    out.append(" ".join("%.7f,%.7f,0" % (lon, lat) for lat, lon, _v, _r in pts))
    out.append("</coordinates></LineString></Placemark>")

    out.append("<Folder><name>%s samples</name>" % sx.escape(metric))
    for lat, lon, val, r in pts:
        desc = "<![CDATA[<b>%s</b>: %s<br/>Cell: %s<br/>Band: %s<br/>Tech: %s<br/>Time: %s]]>" % (
            sx.escape(metric), val if val is not None else "n/a",
            sx.escape(str(r.get("CGI", ""))), sx.escape(str(r.get("Band", ""))),
            sx.escape(str(r.get("Networktech", ""))), sx.escape(str(r.get("Timestamp", ""))))
        out.append(
            "<Placemark><name>%s</name><description>%s</description>"
            '<styleUrl>#b%d</styleUrl><Point><coordinates>%.7f,%.7f,0</coordinates></Point>'
            "</Placemark>" % (val if val is not None else "", desc, _bucket(val), lon, lat))
    out.append("</Folder></Document></kml>")

    with open(kml_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return (kml_path, len(pts))


def summarise_csv(csv_path):
    """Quick statistics over a finished session."""
    import statistics as st
    vals, cells, techs, bands_seen, dist = [], set(), {}, set(), 0.0
    rows = 0
    with open(csv_path, "r", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            rows += 1
            try:
                vals.append(float(r["RSRP"]))
            except (TypeError, ValueError, KeyError):
                pass
            if r.get("CGI"):
                cells.add(r["CGI"])
            t = r.get("Networktech") or "?"
            techs[t] = techs.get(t, 0) + 1
            if r.get("Band"):
                bands_seen.add(r["Band"])
            try:
                dist = max(dist, float(r.get("Distance") or 0))
            except ValueError:
                pass
    out = {"rows": rows, "cells": len(cells), "techs": techs,
           "bands": sorted(bands_seen), "distance_m": dist}
    if vals:
        vals_sorted = sorted(vals)
        out.update(rsrp_min=min(vals), rsrp_max=max(vals), rsrp_avg=st.mean(vals),
                   rsrp_p10=vals_sorted[int(0.10 * (len(vals) - 1))],
                   rsrp_p50=vals_sorted[int(0.50 * (len(vals) - 1))],
                   rsrp_p90=vals_sorted[int(0.90 * (len(vals) - 1))])
    return out
