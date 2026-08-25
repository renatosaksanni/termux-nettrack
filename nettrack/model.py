"""Normalised radio measurement records.

termux-telephony-cellinfo emits slightly different JSON shapes depending on
the Termux:API build and the Android release underneath, and unavailable
values arrive as Integer.MAX_VALUE rather than null. Everything is funnelled
through here so the rest of the app sees one stable schema.
"""

from __future__ import annotations

import time

from . import bands

# Exact "unavailable" sentinels only. A 5G NCI is 36 bits wide, so a blanket
# magnitude test would silently discard real cell identities.
INVALID = frozenset((2147483647, -2147483648,
                     9223372036854775807, -9223372036854775808))

TECHS = ("nr", "lte", "wcdma", "tdscdma", "cdma", "gsm")

# Display order and short label per technology.
TECH_LABEL = {"nr": "5G NR", "lte": "LTE", "wcdma": "UMTS", "tdscdma": "TD-SCDMA",
              "cdma": "CDMA", "gsm": "GSM", None: "--"}


def _clean(v):
    """Drop Android's 'unavailable' sentinels and non-numeric junk."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s or s.lower() in ("null", "none", "unavailable", "n/a", ""):
            return None
        try:
            v = float(s) if ("." in s or "e" in s.lower()) else int(s)
        except ValueError:
            return s
    if isinstance(v, (int, float)):
        if v in INVALID:
            return None
        return v
    return None


def _pick(d, *names):
    """First present, non-sentinel value among alias keys."""
    for n in names:
        if n in d:
            v = _clean(d[n])
            if v is not None:
                return v
    return None


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Cell:
    """One cell as reported by the modem."""

    __slots__ = ("tech", "registered", "mcc", "mnc", "lac", "tac", "cid", "pci",
                 "arfcn", "bandwidth", "rsrp", "rsrq", "sinr", "rssi", "rscp",
                 "ecno", "cqi", "ta", "dbm", "asu", "level", "band", "band_label",
                 "freq_mhz", "duplex", "band_candidates", "raw")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    # -- identity -------------------------------------------------------
    @property
    def plmn(self):
        if self.mcc is None or self.mnc is None:
            return None
        return "%s-%s" % (str(self.mcc).zfill(3), str(self.mnc).zfill(2))

    @property
    def area(self):
        """LAC for 2G/3G, TAC for 4G/5G."""
        return self.tac if self.tac is not None else self.lac

    @property
    def cgi(self):
        if self.plmn is None or self.cid is None:
            return None
        return "%s-%s-%s" % (self.plmn, self.area if self.area is not None else "?", self.cid)

    def node_split(self, gnb_bits=24):
        """Split the cell identity into (node id, sector).

        LTE ECI is 28 bits: 20-bit eNB id + 8-bit cell id.
        NR NCI is 36 bits: gNB id (22-32 bits, operator dependent) + cell id.
        """
        if self.cid is None:
            return (None, None)
        c = int(self.cid)
        if self.tech == "lte":
            return (c >> 8, c & 0xFF)
        if self.tech == "nr":
            shift = max(0, 36 - int(gnb_bits))
            return (c >> shift, c & ((1 << shift) - 1))
        return (None, None)

    # -- signal ---------------------------------------------------------
    @property
    def primary(self):
        """The metric that best represents coverage for this technology."""
        if self.tech in ("lte", "nr"):
            return self.rsrp if self.rsrp is not None else self.dbm
        if self.tech in ("wcdma", "tdscdma"):
            return self.rscp if self.rscp is not None else self.dbm
        return self.rssi if self.rssi is not None else self.dbm

    @property
    def primary_name(self):
        return {"lte": "RSRP", "nr": "SS-RSRP", "wcdma": "RSCP",
                "tdscdma": "RSCP", "gsm": "RSSI", "cdma": "RSSI"}.get(self.tech, "dBm")

    @property
    def quality(self):
        """Coarse 0..4 rating (0 = no service, 4 = excellent)."""
        v = self.primary
        if v is None:
            return 0
        if self.tech in ("lte", "nr"):
            th = (-80, -90, -100, -110)
        elif self.tech in ("wcdma", "tdscdma"):
            th = (-75, -85, -95, -105)
        else:
            th = (-70, -80, -90, -100)
        for i, t in enumerate(th):
            if v >= t:
                return 4 - i
        return 0

    def describe_band(self):
        if self.band_label:
            f = " / %.1f MHz" % self.freq_mhz if self.freq_mhz else ""
            return "%s%s" % (self.band_label, f)
        return "--"

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__ if k != "raw"}


def parse_cell(d, gnb_bits=24):
    """Build a Cell from one termux-telephony-cellinfo entry."""
    if not isinstance(d, dict):
        return None
    tech = (_pick(d, "type", "tech", "technology", "rat") or "").lower()
    for t in TECHS:
        if t in tech:
            tech = t
            break
    else:
        tech = tech or None

    reg = d.get("registered", d.get("isRegistered", d.get("is_registered")))
    if isinstance(reg, str):
        reg = reg.strip().lower() in ("true", "1", "yes")

    mcc = _as_int(_pick(d, "mcc", "mccString", "mcc_string"))
    mnc = _pick(d, "mnc", "mncString", "mnc_string")
    mnc = _as_int(mnc) if mnc is not None else None

    c = Cell(
        tech=tech,
        registered=bool(reg),
        mcc=mcc, mnc=mnc,
        lac=_as_int(_pick(d, "lac", "location_area_code", "locationAreaCode")),
        tac=_as_int(_pick(d, "tac", "tracking_area_code", "trackingAreaCode")),
        cid=_as_int(_pick(d, "ci", "cid", "cell_identity", "cellIdentity",
                          "nci", "eci", "basestation_id", "baseStationId")),
        pci=_as_int(_pick(d, "pci", "psc", "bsic", "physical_cell_id", "physicalCellId")),
        arfcn=_as_int(_pick(d, "earfcn", "nrarfcn", "uarfcn", "arfcn",
                            "channel_number", "channelNumber", "dl_arfcn")),
        bandwidth=_as_int(_pick(d, "bandwidth", "cell_bandwidth", "bandwidthKhz")),
        rsrp=_pick(d, "rsrp", "ss_rsrp", "ssRsrp", "csi_rsrp", "csiRsrp"),
        rsrq=_pick(d, "rsrq", "ss_rsrq", "ssRsrq", "csi_rsrq", "csiRsrq"),
        sinr=_pick(d, "rssnr", "sinr", "ss_sinr", "ssSinr", "csi_sinr", "snr"),
        rssi=_pick(d, "rssi", "signal_strength", "signalStrength"),
        rscp=_pick(d, "rscp", "cpich_rscp"),
        ecno=_pick(d, "ecno", "ec_no", "ecio", "ec_io", "cpich_ecno"),
        cqi=_pick(d, "cqi"),
        ta=_pick(d, "timing_advance", "timingAdvance", "ta"),
        dbm=_pick(d, "dbm", "dBm"),
        asu=_pick(d, "asu", "asu_level", "asuLevel"),
        level=_pick(d, "level", "signal_level"),
        raw=d,
    )
    if c.tech == "lte" and c.rsrp is None and c.dbm is not None:
        c.rsrp = c.dbm
    if c.tech == "nr" and c.rsrp is None and c.dbm is not None:
        c.rsrp = c.dbm
    if c.tech in ("gsm", "cdma") and c.rssi is None and c.dbm is not None:
        c.rssi = c.dbm
    if c.tech in ("wcdma", "tdscdma") and c.rscp is None and c.dbm is not None:
        c.rscp = c.dbm

    info = bands.resolve(c.tech, c.arfcn, c.mcc)
    if info:
        c.band = info.get("band")
        c.band_label = info.get("label")
        c.freq_mhz = info.get("dl_mhz")
        c.duplex = info.get("duplex")
        c.band_candidates = info.get("bands") or ([info.get("label")] if info.get("label") else [])
    else:
        c.band_candidates = []

    # Some backends (adb/dumpsys) report the band the modem is actually on.
    # That is authoritative, and it settles the NR-ARFCN overlaps that no
    # amount of table lookup can resolve.
    reported = d.get("bands")
    if isinstance(reported, (list, tuple)) and reported:
        try:
            num = int(reported[0])
        except (TypeError, ValueError):
            num = None
        if num is not None:
            c.band = num
            c.band_label = ("n%d" % num) if c.tech == "nr" else ("B%d" % num)
            c.band_candidates = [c.band_label]
    return c


def parse_cellinfo(payload, gnb_bits=24):
    """Parse the whole cellinfo array; returns (serving, neighbours)."""
    cells = []
    if isinstance(payload, dict):
        payload = payload.get("cells", payload.get("cellInfo", [payload]))
    if not isinstance(payload, list):
        return (None, [])
    for entry in payload:
        c = parse_cell(entry, gnb_bits)
        if c is not None and (c.tech or c.cid is not None or c.primary is not None):
            cells.append(c)
    serving = next((c for c in cells if c.registered), None)
    if serving is None and cells:
        # Some builds never set `registered`; fall back to the strongest cell
        # that carries a full identity.
        ident = [c for c in cells if c.cid is not None]
        pool = ident or cells
        serving = max(pool, key=lambda c: (c.primary if c.primary is not None else -999))
    neighbours = [c for c in cells if c is not serving]
    neighbours.sort(key=lambda c: (c.primary if c.primary is not None else -999), reverse=True)
    return (serving, neighbours)


class DeviceInfo:
    """Flattened termux-telephony-deviceinfo."""

    __slots__ = ("operator_name", "operator", "network_type", "phone_type",
                 "data_state", "data_activity", "roaming", "sim_state",
                 "sim_operator", "sim_operator_name", "country", "raw")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @classmethod
    def parse(cls, d):
        d = d if isinstance(d, dict) else {}
        g = lambda *n: _pick(d, *n)
        return cls(
            operator_name=g("network_operator_name", "networkOperatorName"),
            operator=g("network_operator", "networkOperator"),
            network_type=(g("network_type", "networkType") or None),
            phone_type=g("phone_type", "phoneType"),
            data_state=g("data_state", "dataState"),
            data_activity=g("data_activity", "dataActivity"),
            roaming=bool(d.get("network_roaming", d.get("networkRoaming", False))),
            sim_state=g("sim_state", "simState"),
            sim_operator=g("sim_operator", "simOperator"),
            sim_operator_name=g("sim_operator_name", "simOperatorName"),
            country=g("network_country_iso", "networkCountryIso"),
            raw=d,
        )


class Fix:
    """A GPS/network location fix."""

    __slots__ = ("lat", "lon", "alt", "accuracy", "speed", "bearing",
                 "provider", "age_ms", "ts")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @classmethod
    def parse(cls, d):
        if not isinstance(d, dict) or "latitude" not in d:
            return None
        return cls(lat=_clean(d.get("latitude")), lon=_clean(d.get("longitude")),
                   alt=_clean(d.get("altitude")), accuracy=_clean(d.get("accuracy")),
                   speed=_clean(d.get("speed")), bearing=_clean(d.get("bearing")),
                   provider=d.get("provider"), age_ms=_clean(d.get("elapsedMs")),
                   ts=time.time())

    @property
    def kmh(self):
        return None if self.speed is None else self.speed * 3.6


class Sample:
    """One timestamped snapshot: everything the app knows at instant T."""

    __slots__ = ("ts", "serving", "neighbours", "device", "fix", "dl_kbps",
                 "ul_kbps", "ping_ms", "site", "distance_m", "site_bearing",
                 "off_boresight", "odometer", "battery", "note")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        if self.ts is None:
            self.ts = time.time()
        if self.neighbours is None:
            self.neighbours = []
