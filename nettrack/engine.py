"""Collector orchestration and session state."""

from __future__ import annotations

import collections
import threading
import time

from . import api, geo, model

HIST = 300  # samples kept for the on-screen graphs


class Event:
    __slots__ = ("ts", "kind", "text")

    def __init__(self, kind, text, ts=None):
        self.ts = ts or time.time()
        self.kind = kind
        self.text = text


class Engine:
    """Owns every background collector and the current radio state.

    The UI only ever reads `snapshot()`, which returns a consistent Sample;
    all mutation happens on collector threads under one lock.
    """

    def __init__(self, source=None, opts=None):
        self.opts = opts or {}
        self.source = source or api
        self.lock = threading.RLock()

        self.serving = None
        self.neighbours = []
        self.device = None
        self.fix = None
        self.battery = {}
        self.dl_kbps = None
        self.ul_kbps = None
        self.ping_ms = None

        self.cellfile = self.opts.get("cellfile") or geo.CellFile()
        self.odometer = geo.Odometer()
        self.site = None
        self.distance_m = None
        self.site_bearing = None
        self.off_boresight = None

        self.hist = {k: collections.deque(maxlen=HIST)
                     for k in ("rsrp", "rsrq", "sinr", "rssi", "speed", "ping")}
        self.events = collections.deque(maxlen=200)
        self.samples = 0
        self.started = time.time()
        self.pollers = []
        self.status = {}
        self.alarms = self.opts.get("alarms")
        self.writer = self.opts.get("writer")
        self._last_ident = None
        self._pending_event = ""

    # ---------------------------------------------------------- lifecycle
    def start(self):
        cadence = float(self.opts.get("interval", 1.0))
        src = self.source
        self.pollers = [
            api.Poller("cell", lambda: src.cellinfo(timeout=max(8.0, cadence * 4)),
                       cadence, self._on_cells, self._err("cell")),
            api.Poller("device", lambda: src.deviceinfo(timeout=10.0),
                       max(4.0, cadence * 4), self._on_device, self._err("device")),
            api.Poller("battery", lambda: src.battery(timeout=10.0),
                       30.0, self._on_battery, self._err("battery")),
        ]
        if self.opts.get("gps", True):
            prov = self.opts.get("gps_provider", "gps")
            req = self.opts.get("gps_request", "last")
            self.pollers.append(
                api.Poller("gps", lambda: src.location(provider=prov, request=req, timeout=30.0),
                           max(2.0, cadence * 2), self._on_fix, self._err("gps")))
        for p in self.pollers:
            p.start()
        return self

    def stop(self):
        for p in self.pollers:
            p.stop()
        for p in self.pollers:
            p.join(timeout=1.5)

    def _err(self, name):
        def handler(msg):
            with self.lock:
                self.status[name] = msg
        return handler

    # ---------------------------------------------------------- callbacks
    def _on_cells(self, payload):
        serving, neighbours = model.parse_cellinfo(
            payload, gnb_bits=int(self.opts.get("gnb_bits", 24)))
        with self.lock:
            self.status.pop("cell", None)
            prev = self.serving
            self.serving = serving
            self.neighbours = neighbours
            self.samples += 1
            self._detect_events(prev, serving)
            self._resolve_site(serving)
            for key, attr in (("rsrp", "rsrp"), ("rsrq", "rsrq"),
                              ("sinr", "sinr"), ("rssi", "rssi")):
                self.hist[key].append(getattr(serving, attr, None) if serving else None)
            sample = self._build_sample()
            evt, self._pending_event = self._pending_event, ""
        if self.alarms and serving:
            for _state, _name, msg in self.alarms.check(serving):
                self.add_event("alarm", msg)
        if self.writer:
            self.writer.write(sample, evt)

    def _on_device(self, payload):
        d = model.DeviceInfo.parse(payload)
        with self.lock:
            self.status.pop("device", None)
            prev = self.device
            self.device = d
            if prev and prev.network_type != d.network_type:
                self.add_event("tech", "Network type %s -> %s" %
                               (prev.network_type or "?", d.network_type or "?"), lock=False)

    def _on_fix(self, payload):
        f = model.Fix.parse(payload)
        if f is None:
            return
        with self.lock:
            self.status.pop("gps", None)
            self.fix = f
            self.odometer.update(f.lat, f.lon)
            self.hist["speed"].append(f.kmh)
            self._resolve_site(self.serving)

    def _on_battery(self, payload):
        with self.lock:
            self.status.pop("battery", None)
            self.battery = payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------ helpers
    def _detect_events(self, prev, cur):
        if cur is None:
            if prev is not None:
                self.add_event("loss", "Lost service", lock=False)
                self._pending_event = "SERVICE_LOST"
            return
        ident = (cur.tech, cur.cgi, cur.pci, cur.arfcn)
        if self._last_ident is None:
            self.add_event("camp", "Camped on %s %s %s" %
                           (model.TECH_LABEL.get(cur.tech, "?"), cur.cgi or "?",
                            cur.band_label or ""), lock=False)
        elif ident != self._last_ident:
            pt, pcgi, ppci, parfcn = self._last_ident
            if pt != cur.tech:
                self.add_event("tech", "%s -> %s" % (model.TECH_LABEL.get(pt, "?"),
                                                     model.TECH_LABEL.get(cur.tech, "?")),
                               lock=False)
                self._pending_event = "TECH_CHANGE"
            elif parfcn != cur.arfcn:
                self.add_event("band", "Band change %s -> %s (ARFCN %s -> %s)" %
                               (prev.band_label if prev else "?", cur.band_label,
                                parfcn, cur.arfcn), lock=False)
                self._pending_event = "BAND_CHANGE"
            else:
                self.add_event("ho", "Handover PCI %s -> %s  (cell %s)" %
                               (ppci, cur.pci, cur.cid), lock=False)
                self._pending_event = "HANDOVER"
        self._last_ident = ident

    def _resolve_site(self, cell):
        self.site = self.distance_m = self.site_bearing = self.off_boresight = None
        if not cell or not len(self.cellfile):
            return
        site = self.cellfile.lookup(cgi=cell.cgi, cid=cell.cid,
                                    arfcn=cell.arfcn, pci=cell.pci)
        if site is None:
            return
        self.site = site
        if self.fix:
            rel = self.cellfile.relate(site, self.fix.lat, self.fix.lon)
            self.distance_m = rel.get("distance_m")
            self.site_bearing = rel.get("bearing")
            self.off_boresight = rel.get("off_boresight")

    def _build_sample(self):
        return model.Sample(
            ts=time.time(), serving=self.serving, neighbours=list(self.neighbours),
            device=self.device, fix=self.fix, dl_kbps=self.dl_kbps, ul_kbps=self.ul_kbps,
            ping_ms=self.ping_ms, site=self.site, distance_m=self.distance_m,
            site_bearing=self.site_bearing, off_boresight=self.off_boresight,
            odometer=self.odometer.total, battery=self.battery)

    def add_event(self, kind, text, lock=True):
        if lock:
            with self.lock:
                self.events.append(Event(kind, text))
        else:
            self.events.append(Event(kind, text))
        if self.writer:
            self.writer.event("[%s] %s" % (kind, text))

    def set_perf(self, dl=None, ul=None, ping=None):
        with self.lock:
            if dl is not None:
                self.dl_kbps = dl
            if ul is not None:
                self.ul_kbps = ul
            if ping is not None:
                self.ping_ms = ping
                self.hist["ping"].append(ping)

    def snapshot(self):
        with self.lock:
            return self._build_sample()

    def health(self):
        """Per-collector liveness, for the status bar."""
        with self.lock:
            return {p.name: {"ok": p.count, "err": p.errors,
                             "last": p.last_ok, "msg": p.last_error}
                    for p in self.pollers}
