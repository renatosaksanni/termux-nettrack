"""ARFCN <-> frequency/band conversion for GSM, UMTS, LTE and NR.

Tables follow 3GPP TS 45.005 (GSM), TS 25.101 (UMTS), TS 36.101 (LTE)
and TS 38.104 (NR). Everything here is pure arithmetic and table lookup,
so it stays testable off-device.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# LTE - TS 36.101 table 5.7.3-1
# (band, fdl_low, ndl_offs, ndl_min, ndl_max, ful_low, nul_offs, nul_min, nul_max, duplex)
# --------------------------------------------------------------------------
_LTE = [
    (1, 2110.0, 0, 0, 599, 1920.0, 18000, 18000, 18599, "FDD"),
    (2, 1930.0, 600, 600, 1199, 1850.0, 18600, 18600, 19199, "FDD"),
    (3, 1805.0, 1200, 1200, 1949, 1710.0, 19200, 19200, 19949, "FDD"),
    (4, 2110.0, 1950, 1950, 2399, 1710.0, 19950, 19950, 20399, "FDD"),
    (5, 869.0, 2400, 2400, 2649, 824.0, 20400, 20400, 20649, "FDD"),
    (6, 875.0, 2650, 2650, 2749, 830.0, 20650, 20650, 20749, "FDD"),
    (7, 2620.0, 2750, 2750, 3449, 2500.0, 20750, 20750, 21449, "FDD"),
    (8, 925.0, 3450, 3450, 3799, 880.0, 21450, 21450, 21799, "FDD"),
    (9, 1844.9, 3800, 3800, 4149, 1749.9, 21800, 21800, 22149, "FDD"),
    (10, 2110.0, 4150, 4150, 4749, 1710.0, 22150, 22150, 22749, "FDD"),
    (11, 1475.9, 4750, 4750, 4949, 1427.9, 22750, 22750, 22949, "FDD"),
    (12, 729.0, 5010, 5010, 5179, 699.0, 23010, 23010, 23179, "FDD"),
    (13, 746.0, 5180, 5180, 5279, 777.0, 23180, 23180, 23279, "FDD"),
    (14, 758.0, 5280, 5280, 5379, 788.0, 23280, 23280, 23379, "FDD"),
    (17, 734.0, 5730, 5730, 5849, 704.0, 23730, 23730, 23849, "FDD"),
    (18, 860.0, 5850, 5850, 5999, 815.0, 23850, 23850, 23999, "FDD"),
    (19, 875.0, 6000, 6000, 6149, 830.0, 24000, 24000, 24149, "FDD"),
    (20, 791.0, 6150, 6150, 6449, 832.0, 24150, 24150, 24449, "FDD"),
    (21, 1495.9, 6450, 6450, 6599, 1447.9, 24450, 24450, 24599, "FDD"),
    (22, 3510.0, 6600, 6600, 7399, 3410.0, 24600, 24600, 25399, "FDD"),
    (23, 2180.0, 7500, 7500, 7699, 2000.0, 25500, 25500, 25699, "FDD"),
    (24, 1525.0, 7700, 7700, 8039, 1626.5, 25700, 25700, 26039, "FDD"),
    (25, 1930.0, 8040, 8040, 8689, 1850.0, 26040, 26040, 26689, "FDD"),
    (26, 859.0, 8690, 8690, 9039, 814.0, 26690, 26690, 27039, "FDD"),
    (27, 852.0, 9040, 9040, 9209, 807.0, 27040, 27040, 27209, "FDD"),
    (28, 758.0, 9210, 9210, 9659, 703.0, 27210, 27210, 27659, "FDD"),
    (29, 717.0, 9660, 9660, 9769, None, None, None, None, "SDL"),
    (30, 2350.0, 9770, 9770, 9869, 2305.0, 27660, 27660, 27759, "FDD"),
    (31, 462.5, 9870, 9870, 9919, 452.5, 27760, 27760, 27809, "FDD"),
    (32, 1452.0, 9920, 9920, 10359, None, None, None, None, "SDL"),
    (33, 1900.0, 36000, 36000, 36199, 1900.0, 36000, 36000, 36199, "TDD"),
    (34, 2010.0, 36200, 36200, 36349, 2010.0, 36200, 36200, 36349, "TDD"),
    (35, 1850.0, 36350, 36350, 36949, 1850.0, 36350, 36350, 36949, "TDD"),
    (36, 1930.0, 36950, 36950, 37549, 1930.0, 36950, 36950, 37549, "TDD"),
    (37, 1910.0, 37550, 37550, 37749, 1910.0, 37550, 37550, 37749, "TDD"),
    (38, 2570.0, 37750, 37750, 38249, 2570.0, 37750, 37750, 38249, "TDD"),
    (39, 1880.0, 38250, 38250, 38649, 1880.0, 38250, 38250, 38649, "TDD"),
    (40, 2300.0, 38650, 38650, 39649, 2300.0, 38650, 38650, 39649, "TDD"),
    (41, 2496.0, 39650, 39650, 41589, 2496.0, 39650, 39650, 41589, "TDD"),
    (42, 3400.0, 41590, 41590, 43589, 3400.0, 41590, 41590, 43589, "TDD"),
    (43, 3600.0, 43590, 43590, 45589, 3600.0, 43590, 43590, 45589, "TDD"),
    (44, 703.0, 45590, 45590, 46589, 703.0, 45590, 45590, 46589, "TDD"),
    (45, 1447.0, 46590, 46590, 46789, 1447.0, 46590, 46590, 46789, "TDD"),
    (46, 5150.0, 46790, 46790, 54539, 5150.0, 46790, 46790, 54539, "TDD"),
    (47, 5855.0, 54540, 54540, 55239, 5855.0, 54540, 54540, 55239, "TDD"),
    (48, 3550.0, 55240, 55240, 56739, 3550.0, 55240, 55240, 56739, "TDD"),
    (49, 3550.0, 56740, 56740, 58239, 3550.0, 56740, 56740, 58239, "TDD"),
    (50, 1432.0, 58240, 58240, 59089, 1432.0, 58240, 58240, 59089, "TDD"),
    (51, 1427.0, 59090, 59090, 59139, 1427.0, 59090, 59090, 59139, "TDD"),
    (52, 3300.0, 59140, 59140, 60139, 3300.0, 59140, 59140, 60139, "TDD"),
    (53, 2483.5, 60140, 60140, 60254, 2483.5, 60140, 60140, 60254, "TDD"),
    (65, 2110.0, 65536, 65536, 66435, 1920.0, 131072, 131072, 131971, "FDD"),
    (66, 2110.0, 66436, 66436, 67335, 1710.0, 131972, 131972, 132671, "FDD"),
    (68, 753.0, 67336, 67336, 67535, 698.0, 132672, 132672, 132971, "FDD"),
    (70, 1995.0, 68336, 68336, 68585, 1695.0, 132972, 132972, 133121, "FDD"),
    (71, 617.0, 68586, 68586, 68935, 663.0, 133122, 133122, 133471, "FDD"),
    (72, 461.0, 68936, 68936, 68985, 451.0, 133472, 133472, 133521, "FDD"),
    (73, 460.0, 68986, 68986, 69035, 450.0, 133522, 133522, 133571, "FDD"),
    (74, 1475.0, 69036, 69036, 69465, 1427.0, 133572, 133572, 134001, "FDD"),
    (75, 1432.0, 69466, 69466, 70315, None, None, None, None, "SDL"),
    (76, 1427.0, 70316, 70316, 70365, None, None, None, None, "SDL"),
    (85, 728.0, 70366, 70366, 70545, 698.0, 134002, 134002, 134181, "FDD"),
    (87, 420.0, 70546, 70546, 70595, 410.0, 134182, 134182, 134231, "FDD"),
    (88, 422.0, 70596, 70596, 70645, 412.0, 134232, 134232, 134281, "FDD"),
]

# --------------------------------------------------------------------------
# NR - TS 38.104 table 5.4.2.3-1 (NR-ARFCN ranges are DL/UL, 5 kHz raster)
# (band, dl_min, dl_max, ul_min, ul_max, duplex)
# --------------------------------------------------------------------------
_NR = [
    ("n1", 422000, 434000, 384000, 396000, "FDD"),
    ("n2", 386000, 398000, 370000, 382000, "FDD"),
    ("n3", 361000, 376000, 342000, 357000, "FDD"),
    ("n5", 173800, 178800, 164800, 169800, "FDD"),
    ("n7", 524000, 538000, 500000, 514000, "FDD"),
    ("n8", 185000, 192000, 176000, 183000, "FDD"),
    ("n12", 145800, 149200, 139800, 143200, "FDD"),
    ("n13", 149200, 151200, 155400, 157400, "FDD"),
    ("n14", 151600, 153600, 157600, 159600, "FDD"),
    ("n18", 172000, 175000, 163000, 166000, "FDD"),
    ("n20", 158200, 164200, 166400, 172400, "FDD"),
    ("n24", 305000, 311800, 305000, 311800, "FDD"),
    ("n25", 386000, 399000, 370000, 383000, "FDD"),
    ("n26", 171800, 178800, 162800, 169800, "FDD"),
    ("n28", 151600, 160600, 140600, 149600, "FDD"),
    ("n29", 143400, 145600, None, None, "SDL"),
    ("n30", 470000, 472000, 461000, 463000, "FDD"),
    ("n34", 402000, 405000, 402000, 405000, "TDD"),
    ("n38", 514000, 524000, 514000, 524000, "TDD"),
    ("n39", 376000, 384000, 376000, 384000, "TDD"),
    ("n40", 460000, 480000, 460000, 480000, "TDD"),
    ("n41", 499200, 537999, 499200, 537999, "TDD"),
    ("n48", 636667, 646666, 636667, 646666, "TDD"),
    ("n50", 286400, 303400, 286400, 303400, "TDD"),
    ("n51", 285400, 286400, 285400, 286400, "TDD"),
    ("n53", 496700, 499000, 496700, 499000, "TDD"),
    ("n65", 422000, 440000, 384000, 402000, "FDD"),
    ("n66", 422000, 440000, 342000, 357000, "FDD"),
    ("n70", 399000, 404000, 339000, 342000, "FDD"),
    ("n71", 123400, 130400, 133600, 140600, "FDD"),
    ("n74", 295000, 303600, 285400, 294000, "FDD"),
    ("n75", 286400, 303400, None, None, "SDL"),
    ("n76", 285400, 286400, None, None, "SDL"),
    ("n77", 620000, 680000, 620000, 680000, "TDD"),
    ("n78", 620000, 653333, 620000, 653333, "TDD"),
    ("n79", 693334, 733333, 693334, 733333, "TDD"),
    ("n90", 499200, 537999, 499200, 537999, "TDD"),
    ("n257", 2054166, 2104165, 2054166, 2104165, "TDD"),
    ("n258", 2016667, 2070832, 2016667, 2070832, "TDD"),
    ("n259", 2270833, 2337499, 2270833, 2337499, "TDD"),
    ("n260", 2229166, 2279165, 2229166, 2279165, "TDD"),
    ("n261", 2070833, 2084999, 2070833, 2084999, "TDD"),
]

# --------------------------------------------------------------------------
# UMTS - TS 25.101 table 5.1 (DL UARFCN ranges, general formula F = 0.2 * N)
# (band, dl_min, dl_max, dl_offset_mhz, ul_min, ul_max, ul_offset_mhz)
# --------------------------------------------------------------------------
_UMTS = [
    (1, 10562, 10838, 0.0, 9612, 9888, 0.0),
    (2, 9662, 9938, 0.0, 9262, 9538, 0.0),
    (3, 1162, 1513, 1575.0, 937, 1288, 1525.0),
    (4, 1537, 1738, 1805.0, 1312, 1513, 1450.0),
    (5, 4357, 4458, 0.0, 4132, 4233, 0.0),
    (6, 4387, 4413, 0.0, 4162, 4188, 0.0),
    (7, 2237, 2563, 2175.0, 2012, 2338, 2100.0),
    (8, 2937, 3088, 340.0, 2712, 2863, 340.0),
    (9, 9237, 9387, 0.0, 8762, 8912, 0.0),
    (10, 3112, 3388, 1490.0, 2887, 3163, 1135.0),
    (11, 3712, 3787, 736.0, 3487, 3562, 733.0),
    (19, 712, 763, 735.0, 312, 363, 770.0),
]

GSM_BANDS = {
    "GSM850": (128, 251, 869.2, 128, 45.0),
    "PGSM900": (1, 124, 935.0, 0, 45.0),
    "EGSM900": (975, 1023, 935.0, 1024, 45.0),
    "DCS1800": (512, 885, 1805.2, 512, 95.0),
    "PCS1900": (512, 810, 1930.2, 512, 80.0),
}


def _round(x, nd=2):
    return None if x is None else round(x + 0.0, nd)


def lte_earfcn(earfcn):
    """Resolve a DL or UL E-UTRA ARFCN.

    Returns dict(band, dl_mhz, ul_mhz, duplex, direction) or None.
    """
    if earfcn is None:
        return None
    try:
        n = int(earfcn)
    except (TypeError, ValueError):
        return None
    for (band, fdl, ndl_o, ndl_a, ndl_b, ful, nul_o, nul_a, nul_b, dup) in _LTE:
        if ndl_a <= n <= ndl_b:
            dl = fdl + 0.1 * (n - ndl_o)
            ul = None if ful is None else (dl - fdl + ful if dup != "TDD" else dl)
            return {"band": band, "label": "B%d" % band, "dl_mhz": _round(dl),
                    "ul_mhz": _round(ul), "duplex": dup, "direction": "DL"}
    for (band, fdl, ndl_o, ndl_a, ndl_b, ful, nul_o, nul_a, nul_b, dup) in _LTE:
        if nul_a is not None and nul_a <= n <= nul_b:
            ul = ful + 0.1 * (n - nul_o)
            return {"band": band, "label": "B%d" % band, "dl_mhz": _round(ul - ful + fdl),
                    "ul_mhz": _round(ul), "duplex": dup, "direction": "UL"}
    return None


def nr_arfcn_to_mhz(nrarfcn):
    """NR-ARFCN -> centre frequency in MHz (TS 38.104 5.4.2.1)."""
    if nrarfcn is None:
        return None
    try:
        n = int(nrarfcn)
    except (TypeError, ValueError):
        return None
    if 0 <= n < 600000:
        return _round(0.0 + 0.005 * n, 3)
    if 600000 <= n < 2016667:
        return _round(3000.0 + 0.015 * (n - 600000), 3)
    if 2016667 <= n <= 3279165:
        return _round(24250.08 + 0.06 * (n - 2016667), 3)
    return None


def nr_arfcn(nrarfcn):
    """Resolve an NR-ARFCN to candidate bands.

    NR band ranges overlap by design (n1/n65/n66, n77/n78), so `bands`
    is a list and `label` is the most likely single pick.
    """
    mhz = nr_arfcn_to_mhz(nrarfcn)
    if mhz is None:
        return None
    n = int(nrarfcn)
    dl, ul = [], []
    for (band, dmin, dmax, umin, umax, dup) in _NR:
        if dmin <= n <= dmax:
            dl.append((dmax - dmin, band))
        if umin is not None and umin <= n <= umax and dup != "TDD":
            ul.append((umax - umin, band))
    # Ranges overlap; the narrowest match is the most specific allocation
    # (e.g. n78 rather than the wider n77 for 3.5 GHz).
    dl = [b for _w, b in sorted(dl)]
    ul = [b for _w, b in sorted(ul)]
    cands = dl or ul
    return {"band": cands[0] if cands else None,
            "label": cands[0] if cands else None,
            "bands": cands, "dl_mhz": mhz, "ul_mhz": mhz,
            "duplex": next((d for (b, _a, _b, _c, _d, d) in _NR if b == (cands[0] if cands else None)), None),
            "direction": "DL" if dl else ("UL" if ul else None)}


def uarfcn(uarfcn_):
    """Resolve a UTRA absolute radio frequency channel number."""
    if uarfcn_ is None:
        return None
    try:
        n = int(uarfcn_)
    except (TypeError, ValueError):
        return None
    for (band, dmin, dmax, doff, umin, umax, uoff) in _UMTS:
        if dmin <= n <= dmax:
            return {"band": band, "label": "B%d" % band,
                    "dl_mhz": _round(doff + 0.2 * n), "ul_mhz": None,
                    "duplex": "FDD", "direction": "DL"}
    for (band, dmin, dmax, doff, umin, umax, uoff) in _UMTS:
        if umin <= n <= umax:
            return {"band": band, "label": "B%d" % band, "dl_mhz": None,
                    "ul_mhz": _round(uoff + 0.2 * n), "duplex": "FDD", "direction": "UL"}
    return None


def gsm_arfcn(arfcn, mcc=None):
    """Resolve a GSM ARFCN. 512-810 is ambiguous between DCS1800 and
    PCS1900; MCC 3xx (North America) disambiguates towards PCS1900.
    """
    if arfcn is None:
        return None
    try:
        n = int(arfcn)
    except (TypeError, ValueError):
        return None
    na = False
    if mcc is not None:
        try:
            na = 310 <= int(mcc) <= 316
        except (TypeError, ValueError):
            na = False
    order = ["GSM850", "PCS1900", "DCS1800", "PGSM900", "EGSM900"] if na else \
            ["GSM850", "PGSM900", "EGSM900", "DCS1800", "PCS1900"]
    for name in order:
        lo, hi, base, off, dup_sep = GSM_BANDS[name]
        if lo <= n <= hi:
            dl = base + 0.2 * (n - off)
            return {"band": name, "label": name, "dl_mhz": _round(dl),
                    "ul_mhz": _round(dl - dup_sep), "duplex": "FDD", "direction": "DL"}
    if n == 0:
        return {"band": "EGSM900", "label": "EGSM900", "dl_mhz": 935.0,
                "ul_mhz": 890.0, "duplex": "FDD", "direction": "DL"}
    return None


def resolve(tech, arfcn, mcc=None):
    """Dispatch on radio technology string."""
    t = (tech or "").lower()
    if t.startswith("nr"):
        return nr_arfcn(arfcn)
    if t.startswith("lte"):
        return lte_earfcn(arfcn)
    if t.startswith("wcdma") or t.startswith("umts") or t.startswith("tdscdma"):
        return uarfcn(arfcn)
    if t.startswith("gsm"):
        return gsm_arfcn(arfcn, mcc)
    return None


def wifi_channel(freq_mhz):
    """Wi-Fi centre frequency (MHz) -> (band, channel)."""
    if not freq_mhz:
        return (None, None)
    f = int(freq_mhz)
    if f == 2484:
        return ("2.4GHz", 14)
    if 2412 <= f <= 2472:
        return ("2.4GHz", (f - 2407) // 5)
    if 5160 <= f <= 5885:
        return ("5GHz", (f - 5000) // 5)
    if 5955 <= f <= 7115:
        return ("6GHz", (f - 5950) // 5)
    if 4915 <= f <= 4980:
        return ("4.9GHz", (f - 4000) // 5)
    return (None, None)
