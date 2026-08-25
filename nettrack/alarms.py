"""Threshold alarms with hysteresis and rate limiting."""

from __future__ import annotations

import time

from . import api

# name -> (attribute, comparison, default threshold, unit)
RULES = {
    "rsrp": ("rsrp", "<", -110.0, "dBm"),
    "rsrq": ("rsrq", "<", -18.0, "dB"),
    "sinr": ("sinr", "<", 0.0, "dB"),
    "rscp": ("rscp", "<", -100.0, "dBm"),
    "rssi": ("rssi", "<", -100.0, "dBm"),
}


class Alarm:
    """One threshold with hysteresis so a marginal signal cannot chatter."""

    def __init__(self, name, threshold, hysteresis=3.0, min_interval=20.0):
        attr, op, default, unit = RULES[name]
        self.name = name
        self.attr = attr
        self.op = op
        self.unit = unit
        self.threshold = float(threshold if threshold is not None else default)
        self.hysteresis = hysteresis
        self.min_interval = min_interval
        self.active = False
        self.last_fired = 0.0
        self.count = 0

    def evaluate(self, cell):
        """Returns 'enter', 'clear' or None."""
        if cell is None:
            return None
        v = getattr(cell, self.attr, None)
        if v is None:
            return None
        below = v < self.threshold
        above = v > self.threshold + self.hysteresis
        if below and not self.active:
            self.active = True
            self.count += 1
            return "enter"
        if above and self.active:
            self.active = False
            return "clear"
        return None

    def should_notify(self, now=None):
        now = now or time.time()
        if now - self.last_fired < self.min_interval:
            return False
        self.last_fired = now
        return True


class AlarmSet:
    """Evaluates all configured alarms and dispatches notifications."""

    def __init__(self, config=None, vibrate=True, notify=True, speak=False, toast=False):
        self.alarms = []
        for name, thr in (config or {}).items():
            if name in RULES and thr is not None:
                self.alarms.append(Alarm(name, thr))
        self.vibrate = vibrate
        self.notify = notify
        self.speak = speak
        self.toast = toast
        self.log = []

    def __bool__(self):
        return bool(self.alarms)

    def check(self, cell):
        """Evaluate every alarm; returns the list of human-readable events."""
        events = []
        for a in self.alarms:
            state = a.evaluate(cell)
            if state is None:
                continue
            v = getattr(cell, a.attr, None)
            if state == "enter":
                msg = "%s %.1f %s below %.1f" % (a.name.upper(), v, a.unit, a.threshold)
                events.append(("enter", a.name, msg))
                if a.should_notify():
                    self._dispatch(msg)
            else:
                msg = "%s recovered (%.1f %s)" % (a.name.upper(), v, a.unit)
                events.append(("clear", a.name, msg))
            self.log.append((time.time(), state, msg))
        return events

    def _dispatch(self, msg):
        if self.vibrate:
            api.vibrate(400)
        if self.notify:
            api.notify("nettrack alarm", msg)
        if self.toast:
            api.toast(msg)
        if self.speak:
            api.speak(msg)
