"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import adbsrc, api, banner, geo, model, netperf, store, views
from .alarms import RULES
from .app import App
from .engine import Engine
from .ui import paint, rule

VERSION = "1.0.0"


def _adb_source(args):
    return adbsrc.AdbSource(serial=getattr(args, "adb_serial", None),
                            host=getattr(args, "adb_host", adbsrc.DEFAULT_HOST),
                            port=getattr(args, "adb_port", None))


def _source(args, announce=True):
    """Pick a data backend.

    'auto' prefers termux-api and falls back to adb when the tools are
    stubbed or missing - which is exactly the Google Play build's failure.
    """
    if args.sim:
        from .sim import SimSource
        return SimSource()
    mode = getattr(args, "source", "auto")
    if mode == "adb":
        return _adb_source(args)
    if mode == "termux":
        return api
    _cells, err = api.cellinfo(timeout=8.0)
    if not err:
        return api
    src = _adb_source(args)
    ok, _detail = src.status()
    if ok:
        if announce:
            print(paint("termux-api unavailable (%s) - using the adb backend"
                        % err.split(":")[-1].strip()[:60], "yellow"), file=sys.stderr)
        return src
    return api


def _cellfile(args):
    if not args.cellfile:
        return geo.CellFile()
    cf = geo.CellFile.load(args.cellfile)
    for e in cf.errors[:3]:
        print(paint("cellfile: " + e, "yellow"), file=sys.stderr)
    if len(cf):
        print(paint("cellfile: %d sites loaded" % len(cf), "grey"), file=sys.stderr)
    return cf


def _alarms(args):
    from .alarms import AlarmSet
    cfg = {}
    for name in RULES:
        v = getattr(args, "alarm_" + name, None)
        if v is not None:
            cfg[name] = v
    if not cfg:
        return None
    return AlarmSet(cfg, vibrate=args.vibrate, notify=not args.no_notify,
                    speak=args.speak, toast=False)


def _engine(args, writer=None, src=None):
    return Engine(src if src is not None else _source(args), {
        "interval": args.interval, "cellfile": _cellfile(args),
        "gps": not args.no_gps, "gps_provider": args.gps_provider,
        "gps_request": args.gps_request, "gnb_bits": args.gnb_bits,
        "alarms": _alarms(args), "writer": writer,
        "logdir": args.logdir, "raw": args.raw,
        "speed_secs": args.speed_secs, "streams": args.streams,
    })


def _preflight(args, src=None):
    """Refuse to start silently broken.

    The check has to be about the backend actually selected: termux-api being
    stubbed is irrelevant once the adb backend is the one serving.
    """
    if args.sim:
        return True
    if isinstance(src, adbsrc.AdbSource):
        ok, detail = src.status()
        if ok:
            return True
        print(paint("cannot start: adb backend selected but not connected", "red", "bold"),
              file=sys.stderr)
        print("  %s\n\nRun `nettrack adb setup`, or `nettrack --sim` to try the "
              "interface without a radio." % detail, file=sys.stderr)
        return False
    rep = api.doctor()
    if rep["fatal"]:
        print(paint("cannot start: no backend can read the radio", "red", "bold"),
              file=sys.stderr)
        # If adb is installed it is almost certainly the intended backend, so
        # lead with why it is not answering rather than with termux-api.
        if adbsrc.shutil.which("adb"):
            _ok, detail = adbsrc.AdbSource().status()
            print(paint("  adb backend: %s" % detail, "yellow"), file=sys.stderr)
            print("    A connection drops when Wireless debugging is toggled, "
                  "when the phone reboots,\n"
                  "    or when Samsung Auto Blocker is switched on - it blocks "
                  "adb entirely.\n"
                  "    Turn Auto Blocker off, then: nettrack adb connect",
                  file=sys.stderr)
        print(paint("  termux-api: stubbed or unavailable in this build", "yellow"),
              file=sys.stderr)
        print("\nRun `nettrack doctor` for the full report, or `nettrack --sim` "
              "to try the interface without a radio.", file=sys.stderr)
        return False
    return True


# ------------------------------------------------------------- subcommands
def cmd_monitor(args):
    src = _source(args)
    if not _preflight(args, src):
        return 2
    writer = None
    if args.log:
        writer = store.SessionWriter(args.logdir, raw=args.raw)
        print("logging to %s" % writer.csv_path)
    eng = _engine(args, writer, src)
    if not args.no_banner:
        banner.show(banner.term_width(),
                    subtitle="simulator" if args.sim else "starting collectors…")
    with api.WakeLock(enabled=not args.no_wakelock and not args.sim):
        eng.start()
        # Let the first sample land so the dashboard opens with real values
        # instead of a frame full of dashes.
        deadline = time.time() + 2.5
        while eng.samples == 0 and time.time() < deadline:
            time.sleep(0.1)
        try:
            App(eng, src, eng.opts).run(fps=args.fps)
        except KeyboardInterrupt:
            pass
        finally:
            eng.stop()
            if eng.writer:
                eng.writer.close()
                print("wrote %d rows -> %s" % (eng.writer.rows, eng.writer.csv_path))
    return 0


def cmd_log(args):
    """Headless logging - no TUI, safe to run under nohup."""
    src = _source(args)
    if not _preflight(args, src):
        return 2
    writer = store.SessionWriter(args.logdir, raw=args.raw, name=args.name)
    eng = _engine(args, writer, src)
    if not args.no_banner:
        banner.show(banner.term_width(), subtitle="headless logging")
    print()
    print("  csv     : %s" % writer.csv_path)
    if writer.raw_path:
        print("  raw     : %s" % writer.raw_path)
    print("  interval: %.1f s     stop with Ctrl-C" % args.interval)
    deadline = time.time() + args.duration if args.duration else None
    with api.WakeLock(enabled=not args.no_wakelock and not args.sim):
        eng.start()
        try:
            last = -1
            while deadline is None or time.time() < deadline:
                time.sleep(1.0)
                if writer.rows != last:
                    last = writer.rows
                    s = eng.snapshot()
                    c = s.serving
                    sys.stdout.write("\r  %d rows  %s  %s %s   " % (
                        writer.rows,
                        (c.cgi if c else "no cell"),
                        (c.primary_name if c else ""),
                        ("%.1f" % c.primary) if c and c.primary is not None else "--"))
                    sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        finally:
            eng.stop()
            writer.close()
    print("\nwrote %d rows -> %s" % (writer.rows, writer.csv_path))
    return 0


def cmd_doctor(args):
    banner.show(banner.term_width(), subtitle="environment check")
    print()
    rep = api.doctor()
    print(rule(60))
    for r in rep["rows"]:
        mark = paint("ok  ", "green") if r["present"] else (
            paint("MISS", "red") if r["required"] else paint("--  ", "yellow"))
        req = "required" if r["required"] else "optional"
        print("  %s %-30s %-8s %s" % (mark, r["cmd"], req, r["purpose"]))
    if rep["live"]:
        print(rule(60))
        for k, v in rep["live"].items():
            good = v == "ok" or v.startswith("ok ")
            print("  %s %-14s %s" % (paint("ok  ", "green") if good else paint("warn", "yellow"), k, v))
    aok, adetail = adbsrc.AdbSource().status()
    print(rule(60))
    print("  %s adb backend    %s" % (paint("ok  ", "green") if aok else paint("--  ", "yellow"),
                                      adetail))
    if not aok:
        print(paint("       run `nettrack adb setup` to use adb instead of termux-api", "grey"))
    if rep.get("warn"):
        print(rule(60))
        for w in rep["warn"]:
            print(paint("  ~ " + w, "yellow"))
    if rep["fatal"]:
        print(rule(60))
        if aok:
            # adb is already serving, so the long "how to fix termux-api"
            # explanation is noise. State the situation in one line.
            print(paint("  ~ termux-api is stubbed in this build; reading the "
                        "radio through adb instead.", "yellow"))
            print(paint("    Ready. Run: nettrack", "green"))
            return 0
        for f in rep["fatal"]:
            print(paint("  ! " + f, "red"))
        print()
        print(paint("  Run `nettrack raw` to see exactly what the tools return.", "grey"))
        return 1
    print(rule(60))
    print(paint("  ready", "green"))
    return 0


def cmd_cells(args):
    """One-shot cell dump, for scripting."""
    src = _source(args)
    payload, err = src.cellinfo()
    if err:
        print(paint(err, "red"), file=sys.stderr)
        return 1
    serving, neigh = model.parse_cellinfo(payload, gnb_bits=args.gnb_bits)
    if args.json:
        print(json.dumps({"serving": serving.to_dict() if serving else None,
                          "neighbours": [n.to_dict() for n in neigh]},
                         indent=2, default=str))
        return 0
    if serving is None:
        print("no cells reported")
        return 1
    node, sector = serving.node_split(args.gnb_bits)
    print(paint("SERVING", "bold", "green"))
    for k, v in (("tech", model.TECH_LABEL.get(serving.tech)), ("cgi", serving.cgi),
                 ("band", serving.describe_band()), ("arfcn", serving.arfcn),
                 ("pci", serving.pci), ("node/sector", "%s / %s" % (node, sector)),
                 (serving.primary_name.lower(), serving.primary),
                 ("rsrq", serving.rsrq), ("sinr", serving.sinr), ("ta", serving.ta)):
        if v is not None:
            print("  %-12s %s" % (k, v))
    if neigh:
        print(paint("\nNEIGHBOURS", "bold"))
        print("  %-6s %-6s %-7s %-8s %s" % ("TECH", "PCI", "ARFCN", "BAND", "LEVEL"))
        for n in neigh:
            print("  %-6s %-6s %-7s %-8s %s" % (
                model.TECH_LABEL.get(n.tech, "?"), n.pci, n.arfcn,
                n.band_label or "--", n.primary))
    return 0


def cmd_speed(args):
    if not args.no_banner:
        banner.show(banner.term_width(), subtitle="throughput test")
        print()
    print(paint("speed test", "bold", "cyan"), "- this consumes mobile data")
    p = netperf.icmp_ping(args.ping_host, count=args.ping_count)
    if p.ok:
        print("  latency  %.0f ms  (min %.0f / max %.0f, jitter %.0f, loss %.0f%%)"
              % (p.avg, p.min, p.max, p.jitter or 0.0, p.loss_pct))
    else:
        print(paint("  latency  unreachable", "red"))
    dns = netperf.dns_lookup()
    print("  dns      %s" % ("%.0f ms" % dns.ms if dns.ok else "failed"))

    def prog(frac, mbps, got):
        sys.stdout.write("\r  down     %-6s  %s" % (netperf.fmt_mbps(mbps),
                                                    netperf.fmt_bytes(got)))
        sys.stdout.flush()

    d = netperf.download(duration=args.speed_secs, streams=args.streams, progress=prog)
    print("\r  down     %-9s (%s in %.1fs, %d streams)   "
          % (netperf.fmt_mbps(d.mbps), netperf.fmt_bytes(d.bytes), d.seconds, d.streams))
    u = netperf.upload(duration=args.speed_secs, streams=max(1, args.streams - 1),
                       progress=lambda f, m, g: None)
    print("  up       %-9s (%s in %.1fs)" % (netperf.fmt_mbps(u.mbps),
                                             netperf.fmt_bytes(u.bytes), u.seconds))
    print("  data used %s" % netperf.fmt_bytes(d.bytes + u.bytes))
    return 0


def cmd_export(args):
    path, n = store.csv_to_kml(args.csv, args.out, metric=args.metric)
    print("wrote %d points -> %s" % (n, path))
    return 0


def cmd_summary(args):
    s = store.summarise_csv(args.csv)
    print(paint(os.path.basename(args.csv), "bold", "cyan"))
    print("  rows        %d" % s["rows"])
    print("  unique cells %d" % s["cells"])
    print("  distance    %s" % geo.fmt_dist(s["distance_m"]))
    print("  bands       %s" % (", ".join(s["bands"]) or "--"))
    print("  tech mix    %s" % ", ".join("%s:%d" % kv for kv in sorted(s["techs"].items())))
    if "rsrp_avg" in s:
        print("  RSRP        avg %.1f   p10 %.1f  p50 %.1f  p90 %.1f   min %.1f  max %.1f"
              % (s["rsrp_avg"], s["rsrp_p10"], s["rsrp_p50"], s["rsrp_p90"],
                 s["rsrp_min"], s["rsrp_max"]))
    return 0


def cmd_band(args):
    """Offline ARFCN lookup - useful on its own, no radio needed."""
    from . import bands as B
    for token in args.arfcn:
        try:
            n = int(token)
        except ValueError:
            print("%-8s -> not a number" % token)
            continue
        if args.tech:
            candidates = [(args.tech, B.resolve(args.tech, n, args.mcc))]
        else:
            candidates = [(t, B.resolve(t, n, args.mcc))
                          for t in ("gsm", "wcdma", "lte", "nr")]
        hits = [(t, r) for t, r in candidates if r and r.get("label")]
        if not hits:
            print("%-8s -> no band match" % token)
            continue
        for tech, r in hits:
            alt = [b for b in (r.get("bands") or [])[1:3]]
            print("%-8s %-6s -> %-8s %10s MHz  %-4s%s" % (
                token, tech.upper(), r["label"],
                r["dl_mhz"] if r["dl_mhz"] is not None else "--",
                r["duplex"] or "",
                "  also " + "/".join(alt) if alt else ""))
    return 0


def cmd_raw(args):
    """Dump exactly what each termux-api tool returns.

    This is the command to run when nettrack reports that it cannot parse a
    tool's output - it shows the bytes verbatim so the cause is visible
    rather than inferred.
    """
    tools = args.tools or ["termux-telephony-cellinfo", "termux-telephony-deviceinfo",
                           "termux-location", "termux-wifi-connectioninfo",
                           "termux-battery-status"]
    for cmd in tools:
        print(paint("$ " + cmd, "bold", "cyan"))
        if not api.have(cmd):
            print(paint("  not installed", "red"))
            print()
            continue
        extra = ["-p", "gps", "-r", "last"] if cmd == "termux-location" else []
        try:
            out, err = api.run(cmd, extra, timeout=args.timeout)
            rc = 0
        except api.ApiError as e:
            print(paint("  error: %s" % e, "red"))
            print()
            continue
        parsed = api._find_json(out.strip())
        okjson = parsed is not api._MISSING
        print("  exit %d   stdout %d bytes   stderr %d bytes   json %s" % (
            rc, len(out), len(err),
            paint("ok", "green") if okjson else paint("NO", "red")))
        if out.strip():
            print(paint("  --- stdout ---", "dark"))
            for line in out.strip().splitlines()[:args.lines]:
                print("  " + line[:args.width])
        if err.strip():
            print(paint("  --- stderr ---", "yellow"))
            for line in err.strip().splitlines()[:args.lines]:
                print("  " + line[:args.width])
        print()
    return 0


def cmd_adb(args):
    action = args.action
    src = _adb_source(args)

    if action == "setup":
        banner.show(banner.term_width(), subtitle="adb backend setup")
        print()
        print(adbsrc.SETUP_GUIDE)
        return 0

    if action == "discover":
        cands = adbsrc.candidate_endpoints(
            progress=lambda h: print(paint("  scanning %s ports %d-%d …"
                                           % (h, *adbsrc.SCAN_RANGE), "dark")))
        if not cands:
            print(paint("nothing listening", "yellow"))
            print("  Turn Wireless debugging ON, connect Wi-Fi, and switch off\n"
                  "  Samsung Auto Blocker (Settings > Security and privacy).")
            return 1
        for host, port, how in cands:
            print("  %s:%-6d  (%s)" % (host, port, how))
        print(paint("\n  Use these with:  nettrack adb pair <PORT> <CODE>", "grey"))
        return 0

    if action == "pair":
        # `pair <CODE>` discovers the port; `pair <PORT> <CODE>` is still honoured.
        if len(args.params) == 1:
            # Only the code was given: find the listening port ourselves and
            # try each candidate, since adb here has no mDNS backend.
            code = args.params[0]
            print("looking for the pairing port …")
            cands = adbsrc.candidate_endpoints(
                progress=lambda h: print(paint("  scanning %s …" % h, "dark")))
            if not cands:
                print(paint("nothing listening - keep the pairing dialog OPEN "
                            "and check Auto Blocker is off.", "red"))
                return 1
            for host, port, _how in cands:
                out, err, rc = adbsrc.adb(["pair", "%s:%s" % (host, port), code],
                                          timeout=60.0)
                text = (out + err).strip()
                if rc == 0 and "success" in text.lower():
                    print(paint(text, "green"))
                    return 0
                print(paint("  %s:%d -> %s" % (host, port, text.splitlines()[0]
                                               if text else "no response"), "dark"))
            print(paint("none of the %d candidate ports accepted that code."
                        % len(cands), "red"))
            print("  The code expires when the dialog closes - reopen it and "
                  "read a fresh one.")
            return 1
        elif len(args.params) >= 2:
            host, port, code = args.adb_host, args.params[0], args.params[1]
        else:
            print("usage: nettrack adb pair <CODE>   (or: pair <PAIR_PORT> <CODE>)",
                  file=sys.stderr)
            return 2
        print("pairing with %s:%s …" % (host, port))
        out, err, rc = adbsrc.adb(["pair", "%s:%s" % (host, port), code], timeout=60.0)
        text = (out + err).strip()
        ok = rc == 0 and "success" in text.lower()
        print(paint(text or "no output", "green" if ok else "red"))
        if not ok:
            print(paint("  The port and code change every time that dialog is "
                        "reopened - read both fresh, and keep it open.", "grey"))
        return 0 if ok else 1

    if action == "connect":
        if args.params:
            host, port = args.adb_host, args.params[0]
        elif args.adb_port:
            host, port = args.adb_host, args.adb_port
        else:
            print("looking for the connect port …")
            cands = adbsrc.candidate_endpoints(
                progress=lambda h: print(paint("  scanning %s …" % h, "dark")))
            for host, port, _how in cands:
                ok, text = adbsrc.connect(host, port)
                if ok:
                    print(paint(text, "green"))
                    return 0
                print(paint("  %s:%d -> %s" % (host, port, text.splitlines()[0]
                                               if text else "?"), "dark"))
            print(paint("no endpoint accepted a connection - pair first.", "yellow"))
            return 1
        ok, text = adbsrc.connect(host, port)
        print(paint(text, "green" if ok else "red"))
        return 0 if ok else 1

    # default: status
    ok, detail = src.status()
    print("%s %s" % (paint("adb", "bold", "cyan"),
                     paint(detail, "green" if ok else "yellow")))
    if not ok:
        print()
        print(adbsrc.SETUP_GUIDE)
        return 1
    cells, err = src.cellinfo(timeout=20.0)
    if err:
        print(paint("  cellinfo: " + err, "red"))
        return 1
    serving, _neigh = model.parse_cellinfo(cells)
    print(paint("  cellinfo: ok - %d cell%s" % (len(cells), "" if len(cells) == 1 else "s"),
                "green"))
    if serving:
        print("  serving : %s %s  %s  %s %s" % (
            model.TECH_LABEL.get(serving.tech, "?"), serving.cgi or "",
            serving.describe_band(), serving.primary_name, serving.primary))
    return 0


# ------------------------------------------------------------------ parser
def build_parser():
    p = argparse.ArgumentParser(
        prog="nettrack",
        description="Cellular network monitor and drive-test logger for Termux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `nettrack doctor` first. Add --sim to try it without a radio.")
    p.add_argument("--version", action="version", version="nettrack " + VERSION)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-i", "--interval", type=float, default=1.0,
                        metavar="S", help="cell sampling interval (default 1.0)")
    common.add_argument("--sim", action="store_true", help="use the built-in simulator")
    common.add_argument("--source", choices=("auto", "termux", "adb"), default="auto",
                        help="data backend (default auto: termux-api, else adb)")
    common.add_argument("--adb-host", default=adbsrc.DEFAULT_HOST)
    common.add_argument("--adb-port", default=None, help="adb port on localhost")
    common.add_argument("--adb-serial", default=None, help="explicit adb device serial")
    common.add_argument("--cellfile", metavar="PATH",
                        help="CSV of known sites for distance/azimuth")
    common.add_argument("--gnb-bits", type=int, default=24, metavar="N",
                        help="gNB id length when splitting an NR NCI (default 24)")
    common.add_argument("--no-gps", action="store_true", help="disable location collector")
    common.add_argument("--gps-provider", default="gps", choices=("gps", "network", "passive"))
    common.add_argument("--gps-request", default="last", choices=("last", "once"))
    common.add_argument("--logdir", metavar="DIR", default=None,
                        help="log directory (default %s)" % store.default_log_dir())
    common.add_argument("--raw", action="store_true", help="also write raw JSONL")
    common.add_argument("--no-wakelock", action="store_true")
    common.add_argument("--no-banner", action="store_true", help="skip the start-up logo")
    common.add_argument("--speed-secs", type=float, default=8.0, metavar="S")
    common.add_argument("--streams", type=int, default=4, metavar="N")
    common.add_argument("--ping-host", default=netperf.DEFAULT_PING_HOST)
    common.add_argument("--ping-count", type=int, default=5)
    for name, (_a, _o, default, unit) in RULES.items():
        common.add_argument("--alarm-" + name, type=float, metavar="V",
                            help="alarm when %s < V %s (suggested %g)" % (name.upper(), unit, default))
    common.add_argument("--vibrate", action="store_true", help="vibrate on alarm")
    common.add_argument("--speak", action="store_true", help="speak alarms via TTS")
    common.add_argument("--no-notify", action="store_true")

    sub = p.add_subparsers(dest="cmd")

    m = sub.add_parser("monitor", parents=[common], help="live dashboard (default)")
    m.add_argument("--log", action="store_true", help="start logging immediately")
    m.add_argument("--fps", type=float, default=4.0, help="redraw rate")
    m.set_defaults(func=cmd_monitor)

    lg = sub.add_parser("log", parents=[common], help="headless CSV logging")
    lg.add_argument("--duration", type=float, default=0, metavar="S",
                    help="stop after S seconds (0 = until Ctrl-C)")
    lg.add_argument("--name", default=None, help="session file name")
    lg.set_defaults(func=cmd_log)

    d = sub.add_parser("doctor", help="check the Termux:API environment")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("cells", parents=[common], help="one-shot cell dump")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_cells)

    sp = sub.add_parser("speed", parents=[common], help="latency + throughput test")
    sp.set_defaults(func=cmd_speed)

    ex = sub.add_parser("export", help="convert a session CSV to KML")
    ex.add_argument("csv")
    ex.add_argument("-o", "--out", default=None)
    ex.add_argument("--metric", default="RSRP")
    ex.set_defaults(func=cmd_export)

    sm = sub.add_parser("summary", help="statistics for a session CSV")
    sm.add_argument("csv")
    sm.set_defaults(func=cmd_summary)

    ad = sub.add_parser("adb", help="set up / check the adb backend")
    ad.add_argument("action", nargs="?", default="status",
                    choices=("status", "setup", "discover", "pair", "connect"))
    ad.add_argument("params", nargs="*")
    ad.add_argument("--adb-host", default=adbsrc.DEFAULT_HOST)
    ad.add_argument("--adb-port", default=None)
    ad.add_argument("--adb-serial", default=None)
    ad.set_defaults(func=cmd_adb)

    rw = sub.add_parser("raw", help="dump raw termux-api output (diagnostics)")
    rw.add_argument("tools", nargs="*", help="tool names (default: the main ones)")
    rw.add_argument("--timeout", type=float, default=15.0)
    rw.add_argument("--lines", type=int, default=12, help="max lines per stream")
    rw.add_argument("--width", type=int, default=160, help="max chars per line")
    rw.set_defaults(func=cmd_raw)

    b = sub.add_parser("band", help="offline ARFCN -> band/frequency lookup")
    b.add_argument("arfcn", nargs="+")
    b.add_argument("--tech", choices=("gsm", "wcdma", "lte", "nr"))
    b.add_argument("--mcc", type=int, default=None)
    b.set_defaults(func=cmd_band)

    return p, common


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser, common = build_parser()
    # Bare `nettrack` (or only flags) means `monitor`.
    known = {"monitor", "log", "doctor", "cells", "speed", "export", "summary",
             "band", "raw", "adb"}
    if not argv or (argv[0] not in known and not argv[0] in ("-h", "--help", "--version")):
        argv = ["monitor"] + argv
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
