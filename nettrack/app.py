"""Interactive TUI application loop."""

from __future__ import annotations

import threading
import time

from . import api, netperf, store, views
from .engine import Engine
from .ui import Keys, Screen, paint


class App:
    def __init__(self, engine, source=None, opts=None):
        self.engine = engine
        self.source = source or api
        self.opts = opts or {}
        self.screen_name = "dash"
        self.wifi = {"conn": {}, "scan": [], "error": None, "ts": 0.0}
        self.busy = None          # text shown while a blocking task runs
        self.running = True
        self._task = None

    # ------------------------------------------------------------ tasks
    def _spawn(self, label, fn):
        """Run a blocking measurement off the UI thread."""
        if self._task and self._task.is_alive():
            return
        self.busy = label

        def wrap():
            try:
                fn()
            except Exception as e:
                self.engine.add_event("alarm", "%s failed: %s" % (label, e))
            finally:
                self.busy = None

        self._task = threading.Thread(target=wrap, daemon=True)
        self._task.start()

    def speed_test(self):
        def run():
            self.busy = "speed test: downlink…"
            d = netperf.download(duration=float(self.opts.get("speed_secs", 8)),
                                 streams=int(self.opts.get("streams", 4)))
            self.engine.set_perf(dl=d.mbps * 1000.0 if d.ok else None)
            self.busy = "speed test: uplink…"
            u = netperf.upload(duration=float(self.opts.get("speed_secs", 8)),
                               streams=max(1, int(self.opts.get("streams", 4)) - 1))
            self.engine.set_perf(ul=u.mbps * 1000.0 if u.ok else None)
            self.engine.add_event("perf", "DL %s / UL %s  (%s used)" % (
                netperf.fmt_mbps(d.mbps), netperf.fmt_mbps(u.mbps),
                netperf.fmt_bytes(d.bytes + u.bytes)))
        self._spawn("speed test", run)

    def ping_test(self):
        def run():
            r = netperf.icmp_ping(self.opts.get("ping_host", netperf.DEFAULT_PING_HOST),
                                  count=int(self.opts.get("ping_count", 5)))
            if r.ok:
                self.engine.set_perf(ping=r.avg)
                self.engine.add_event("perf", "RTT avg %.0f ms  jitter %.0f ms  loss %.0f%%"
                                      % (r.avg, r.jitter or 0.0, r.loss_pct))
            else:
                self.engine.add_event("alarm", "ping to %s failed" % r.host)
        self._spawn("ping", run)

    def wifi_scan(self):
        def run():
            conn, cerr = self.source.wifi_connection()
            scan, serr = self.source.wifi_scan()
            self.wifi = {"conn": conn or {}, "scan": scan or [],
                         "error": serr or cerr, "ts": time.time()}
        self._spawn("wifi scan", run)

    def toggle_log(self):
        eng = self.engine
        if eng.writer:
            path = eng.writer.csv_path
            rows = eng.writer.rows
            eng.writer.close()
            eng.writer = None
            eng.add_event("log", "stopped: %d rows -> %s" % (rows, path))
        else:
            eng.writer = store.SessionWriter(self.opts.get("logdir"),
                                             raw=bool(self.opts.get("raw")))
            eng.add_event("log", "logging to %s" % eng.writer.csv_path)

    # ------------------------------------------------------------- loop
    def handle_key(self, k):
        if k in ("q", "\x03", "\x04"):
            self.running = False
        elif k in ("1", "d"):
            self.screen_name = "dash"
        elif k in ("2", "c"):
            self.screen_name = "cells"
        elif k in ("3", "w"):
            self.screen_name = "wifi"
            if time.time() - self.wifi["ts"] > 15:
                self.wifi_scan()
        elif k in ("4", "e"):
            self.screen_name = "events"
        elif k in ("h", "?"):
            self.screen_name = "help"
        elif k == "s":
            self.speed_test()
        elif k == "p":
            self.ping_test()
        elif k == "r" and self.screen_name == "wifi":
            self.wifi_scan()
        elif k == "L":
            self.toggle_log()
        elif self.screen_name == "help":
            self.screen_name = "dash"

    def render(self, w, h):
        e = self.engine
        if self.screen_name == "help":
            return views.help_screen(w, h)
        if self.screen_name == "cells":
            lines = views.cells_screen(e, w, h)
        elif self.screen_name == "wifi":
            lines = views.wifi_screen(e, w, h, self.wifi)
        elif self.screen_name == "events":
            lines = views.events_screen(e, w, h)
        else:
            lines = views.dashboard(e, w, h)
        if self.busy:
            lines[-1] = paint(" ⏳ %s " % self.busy, "bold", "bgblue")
        elif e.writer:
            lines[-1] = paint(" ● REC %d " % e.writer.rows, "bold", "red") + lines[-1]
        return lines

    def run(self, fps=4.0):
        period = 1.0 / fps
        with Screen() as scr, Keys() as keys:
            while self.running:
                scr.poll_resize()
                scr.draw(self.render(scr.w, scr.h))
                t_end = time.time() + period
                while time.time() < t_end:
                    k = keys.get(timeout=max(0.01, t_end - time.time()))
                    if k:
                        self.handle_key(k)
                        break
