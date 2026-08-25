"""Synthetic data source.

Lets the whole app - collectors, alarms, logging, TUI - be exercised on a
machine with no radio, which is also how the layout gets tested off-device.
"""

from __future__ import annotations

import math
import random
import time

_T0 = time.time()


class SimSource:
    """Drop-in replacement for the `api` module's collector functions."""

    name = "simulator"

    def __init__(self, seed=7, tech="lte"):
        self.rnd = random.Random(seed)
        self.tech = tech
        self.t0 = time.time()
        self.lat, self.lon = -6.2000, 106.8167   # central Jakarta
        self.heading = 78.0
        self.speed = 11.0                        # m/s, ~40 km/h
        self.sites = [
            (1234, 301, 1650, -6.1985, 106.8150, "JKT_MENTENG_1"),
            (1235, 115, 1650, -6.2035, 106.8215, "JKT_CIKINI_2"),
            (1236, 442, 3050, -6.1960, 106.8280, "JKT_SALEMBA_3"),
            (1237, 88, 9410, -6.2080, 106.8100, "JKT_KEBON_4"),
        ]
        self.serving_idx = 0
        self._last = time.time()

    # -- movement -------------------------------------------------------
    def _advance(self):
        now = time.time()
        dt = min(5.0, now - self._last)
        self._last = now
        self.heading += self.rnd.uniform(-4, 4)
        self.speed = max(0.0, min(30.0, self.speed + self.rnd.uniform(-1.2, 1.2)))
        d = self.speed * dt
        self.lat += (d * math.cos(math.radians(self.heading))) / 111320.0
        self.lon += (d * math.sin(math.radians(self.heading))) / (
            111320.0 * math.cos(math.radians(self.lat)))

    def _rsrp_from(self, site):
        from .geo import haversine
        d = max(20.0, haversine(self.lat, self.lon, site[3], site[4]) or 500.0)
        # free-space-ish path loss plus shadow fading
        return -32.0 - 35.0 * math.log10(d / 20.0) + self.rnd.gauss(0, 2.0)

    def _reselect(self):
        best = max(range(len(self.sites)), key=lambda i: self._rsrp_from(self.sites[i]))
        if best != self.serving_idx and self.rnd.random() < 0.5:
            self.serving_idx = best

    # -- api surface ----------------------------------------------------
    def cellinfo(self, timeout=10.0):
        self._advance()
        self._reselect()
        out = []
        for i, s in enumerate(self.sites):
            cid, pci, earfcn, slat, slon, _name = s
            rsrp = self._rsrp_from(s)
            entry = {
                "type": "lte", "registered": i == self.serving_idx,
                "ci": cid * 256 + (i + 1), "pci": pci, "tac": 4521,
                "mcc": 510, "mnc": 10, "earfcn": earfcn, "bandwidth": 20000,
                "rsrp": round(rsrp, 1),
                "rsrq": round(-8.0 - self.rnd.random() * 9.0, 1),
                "dbm": round(rsrp, 1),
                "asu": max(0, min(97, int(rsrp + 140))),
                "level": max(0, min(4, int((rsrp + 120) / 10))),
            }
            if i == self.serving_idx:
                entry["rssnr"] = round(self.rnd.uniform(-3, 24), 1)
                entry["cqi"] = self.rnd.choice([2147483647, 8, 11, 13, 15])
                entry["timing_advance"] = self.rnd.randint(0, 30)
            out.append(entry)
        # occasional 5G NR neighbour, as an NSA anchor would report
        if self.rnd.random() < 0.35:
            out.append({"type": "nr", "registered": False, "nci": 908123456,
                        "pci": 512, "tac": 4521, "mcc": 510, "mnc": 10,
                        "nrarfcn": 632628, "ss_rsrp": round(self.rnd.uniform(-115, -85), 1),
                        "ss_rsrq": round(self.rnd.uniform(-17, -9), 1),
                        "ss_sinr": round(self.rnd.uniform(-2, 20), 1)})
        return (out, None)

    def deviceinfo(self, timeout=10.0):
        return ({"network_operator_name": "Telkomsel", "network_operator": "51010",
                 "network_type": "lte" if self.rnd.random() > 0.15 else "nr_nsa",
                 "phone_type": "gsm", "data_state": "connected",
                 "data_activity": self.rnd.choice(["none", "in", "out", "inout"]),
                 "network_roaming": False, "sim_state": "ready",
                 "sim_operator_name": "TELKOMSEL", "network_country_iso": "id"}, None)

    def location(self, provider="gps", request="last", timeout=25.0):
        return ({"latitude": self.lat, "longitude": self.lon,
                 "altitude": 12.0 + self.rnd.uniform(-2, 2),
                 "accuracy": round(self.rnd.uniform(3, 12), 1),
                 "speed": round(self.speed, 2), "bearing": round(self.heading % 360, 1),
                 "provider": "gps", "elapsedMs": 400}, None)

    def battery(self, timeout=10.0):
        el = time.time() - self.t0
        return ({"percentage": max(1, int(84 - el / 90)), "temperature": 31.5 + el / 600.0,
                 "status": "DISCHARGING"}, None)

    def wifi_connection(self, timeout=10.0):
        return ({"ssid": "SimNet-5G", "bssid": "a4:2b:8c:11:22:33", "rssi": -52,
                 "frequency_mhz": 5180, "link_speed_mbps": 433, "ip": "192.168.1.42",
                 "supplicant_state": "COMPLETED"}, None)

    def wifi_scan(self, timeout=20.0):
        aps = []
        for i in range(9):
            aps.append({"ssid": ["SimNet-5G", "IndiHome-A1", "Tsel-Orbit", "Kos_Wifi",
                                 "ZTE_2.4G", "@wifi.id", "Hidden", "MyRepublic",
                                 "Biznet-Home"][i],
                        "bssid": "a4:2b:8c:%02x:%02x:%02x" % (i, i * 7 % 256, i * 13 % 256),
                        "rssi": int(self.rnd.uniform(-90, -35)),
                        "frequency_mhz": self.rnd.choice([2412, 2437, 2462, 5180, 5745, 5220]),
                        "channel_bandwidth_mhz": self.rnd.choice([20, 40, 80])})
        return (aps, None)
