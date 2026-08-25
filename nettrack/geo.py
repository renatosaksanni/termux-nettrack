"""Geodesy helpers and G-NetTrack style cell-site database."""

from __future__ import annotations

import csv
import io
import math
import os

EARTH_R = 6371008.8  # mean earth radius, metres


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_diff(a, b):
    """Smallest signed difference b-a in degrees, within (-180, 180]."""
    if a is None or b is None:
        return None
    d = (b - a + 180.0) % 360.0 - 180.0
    return d + 360.0 if d <= -180.0 else d


def compass(deg):
    if deg is None:
        return "--"
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((deg % 360) / 22.5 + 0.5) % 16]


def fmt_dist(m):
    if m is None:
        return "--"
    return "%.0f m" % m if m < 1000 else "%.2f km" % (m / 1000.0)


class Site:
    __slots__ = ("name", "cgi", "cid", "lac", "tac", "pci", "arfcn", "tech",
                 "lat", "lon", "azimuth", "height", "raw")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __repr__(self):
        return "<Site %s cid=%s %.5f,%.5f az=%s>" % (
            self.name, self.cid, self.lat or 0, self.lon or 0, self.azimuth)


# Column aliases seen across G-NetTrack / Nemo / in-house cell files.
_ALIAS = {
    "name": ("cellname", "name", "sitename", "site", "cell", "cell_name", "label"),
    "cgi": ("cgi", "ecgi", "nci", "globalcellid", "global_cell_id"),
    "cid": ("cid", "cellid", "cell_id", "ci", "eci", "enodeb_cellid", "localcellid"),
    "lac": ("lac", "lac_id"),
    "tac": ("tac", "tac_id"),
    "pci": ("pci", "psc", "bsic", "pci_psc"),
    "arfcn": ("arfcn", "earfcn", "uarfcn", "nrarfcn", "dl_arfcn", "channel"),
    "tech": ("tech", "technology", "rat", "networktech", "type"),
    "lat": ("lat", "latitude", "y"),
    "lon": ("lon", "long", "longitude", "x"),
    "azimuth": ("azimuth", "az", "bearing", "direction", "ant_azimuth"),
    "height": ("height", "alt", "altitude", "ant_height", "h"),
}


def _norm(h):
    return "".join(ch for ch in h.strip().lower() if ch.isalnum() or ch == "_")


def _num(v):
    if v is None:
        return None
    v = str(v).strip().replace(",", ".")
    if not v or v.lower() in ("null", "none", "-", "n/a", "na"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v):
    f = _num(v)
    return None if f is None else int(f)


class CellFile:
    """Load a cell-site table and look sites up by identity.

    Accepts comma, semicolon or tab separated files with a header row.
    Indexes on CGI, on cell id, and on (arfcn, pci) so a site can be
    matched whichever identifiers the radio reports.
    """

    def __init__(self):
        self.sites = []
        self.by_cgi = {}
        self.by_cid = {}
        self.by_pci = {}
        self.path = None
        self.errors = []

    def __len__(self):
        return len(self.sites)

    @classmethod
    def load(cls, path):
        cf = cls()
        cf.path = path
        if not path or not os.path.exists(path):
            cf.errors.append("cellfile not found: %s" % path)
            return cf
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        if not text.strip():
            return cf
        head = text.splitlines()[0]
        delim = max((";", ",", "\t"), key=head.count)
        if head.count(delim) == 0:
            delim = ","
        rdr = csv.reader(io.StringIO(text), delimiter=delim)
        try:
            header = [_norm(h) for h in next(rdr)]
        except StopIteration:
            return cf
        colmap = {}
        for idx, h in enumerate(header):
            for field, names in _ALIAS.items():
                if h in names and field not in colmap:
                    colmap[field] = idx
        if "lat" not in colmap or "lon" not in colmap:
            cf.errors.append("cellfile has no latitude/longitude column")
            return cf
        for lineno, row in enumerate(rdr, start=2):
            if not row or all(not c.strip() for c in row):
                continue

            def get(field):
                i = colmap.get(field)
                return row[i] if i is not None and i < len(row) else None

            lat, lon = _num(get("lat")), _num(get("lon"))
            if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                cf.errors.append("line %d: bad coordinates" % lineno)
                continue
            s = Site(name=(get("name") or "").strip() or None,
                     cgi=(get("cgi") or "").strip() or None,
                     cid=_int(get("cid")), lac=_int(get("lac")), tac=_int(get("tac")),
                     pci=_int(get("pci")), arfcn=_int(get("arfcn")),
                     tech=(get("tech") or "").strip().lower() or None,
                     lat=lat, lon=lon, azimuth=_num(get("azimuth")),
                     height=_num(get("height")), raw=row)
            cf.sites.append(s)
            if s.cgi:
                cf.by_cgi.setdefault(s.cgi.upper().replace("-", ""), s)
            if s.cid is not None:
                cf.by_cid.setdefault(s.cid, s)
            if s.pci is not None and s.arfcn is not None:
                cf.by_pci.setdefault((s.arfcn, s.pci), s)
        return cf

    def lookup(self, cgi=None, cid=None, arfcn=None, pci=None):
        """Best-effort match, most specific identifier first."""
        if cgi:
            s = self.by_cgi.get(str(cgi).upper().replace("-", ""))
            if s:
                return s
        if cid is not None:
            s = self.by_cid.get(int(cid))
            if s:
                return s
            # LTE ECI packs eNB id in the high bits; try the whole-site id too.
            s = self.by_cid.get(int(cid) >> 8)
            if s:
                return s
        if arfcn is not None and pci is not None:
            s = self.by_pci.get((int(arfcn), int(pci)))
            if s:
                return s
        return None

    def relate(self, site, lat, lon):
        """Distance/azimuth from a GPS fix to a site, plus antenna offset."""
        if site is None or lat is None or lon is None:
            return {}
        d = haversine(lat, lon, site.lat, site.lon)
        br = bearing(lat, lon, site.lat, site.lon)
        out = {"site": site, "distance_m": d, "bearing": br, "compass": compass(br)}
        if site.azimuth is not None and br is not None:
            # Angle between the antenna boresight and the direction to us.
            out["off_boresight"] = angle_diff(site.azimuth, (br + 180.0) % 360.0)
        return out


class Odometer:
    """Accumulates travelled distance from a stream of GPS fixes."""

    def __init__(self, min_step=3.0):
        self.total = 0.0
        self.min_step = min_step
        self._last = None

    def update(self, lat, lon):
        if lat is None or lon is None:
            return self.total
        if self._last is not None:
            d = haversine(self._last[0], self._last[1], lat, lon) or 0.0
            if d >= self.min_step:
                self.total += d
                self._last = (lat, lon)
        else:
            self._last = (lat, lon)
        return self.total
