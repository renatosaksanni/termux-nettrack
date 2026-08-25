"""Active latency and throughput measurement.

Android blocks /proc/net for untrusted apps, so device-wide byte counters
(what TrafficStats gives a native app) are not readable from Termux. The
only honest option from a shell is to generate traffic ourselves and time
it, which is what this module does. Every function reports the bytes it
moved so the caller can show data cost.
"""

from __future__ import annotations

import socket
import ssl
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request

DEFAULT_DOWN = "https://speed.cloudflare.com/__down?bytes={bytes}"
DEFAULT_UP = "https://speed.cloudflare.com/__up"
DEFAULT_PING_HOST = "1.1.1.1"

UA = "nettrack/1.0 (termux)"


class Result(dict):
    """Plain dict with attribute access, so views stay readable."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


# ----------------------------------------------------------------- latency
def tcp_ping(host=DEFAULT_PING_HOST, port=443, count=5, timeout=2.0, interval=0.2):
    """Connect-time latency. Works without root, unlike ICMP."""
    samples, lost = [], 0
    for _ in range(count):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        t0 = time.perf_counter()
        try:
            s.connect((host, port))
            samples.append((time.perf_counter() - t0) * 1000.0)
        except OSError:
            lost += 1
        finally:
            try:
                s.close()
            except OSError:
                pass
        time.sleep(interval)
    return _summarise(samples, lost, count, host, "tcp:%d" % port)


def icmp_ping(host=DEFAULT_PING_HOST, count=5, timeout=2.0):
    """Use the system ping binary when present; falls back to TCP."""
    try:
        p = subprocess.run(["ping", "-c", str(count), "-W", str(int(timeout)), host],
                           capture_output=True, text=True, timeout=count * (timeout + 1) + 5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return tcp_ping(host, count=count, timeout=timeout)
    samples = []
    for line in p.stdout.splitlines():
        if "time=" in line:
            try:
                samples.append(float(line.split("time=")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    lost = count - len(samples)
    return _summarise(samples, lost, count, host, "icmp")


def _summarise(samples, lost, count, host, method):
    if not samples:
        return Result(host=host, method=method, sent=count, lost=lost, loss_pct=100.0,
                      min=None, avg=None, max=None, jitter=None, ok=False)
    jitter = None
    if len(samples) > 1:
        jitter = statistics.mean(abs(b - a) for a, b in zip(samples, samples[1:]))
    return Result(host=host, method=method, sent=count, lost=lost,
                  loss_pct=100.0 * lost / count if count else 0.0,
                  min=min(samples), avg=statistics.mean(samples), max=max(samples),
                  jitter=jitter, ok=True)


# -------------------------------------------------------------- throughput
def _opener():
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def _stream_down(url, deadline, counter, lock, chunk=65536):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _opener().open(req, timeout=15.0) as resp:
            while time.monotonic() < deadline:
                buf = resp.read(chunk)
                if not buf:
                    break
                with lock:
                    counter[0] += len(buf)
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
        pass


def download(duration=8.0, streams=4, url_tpl=DEFAULT_DOWN, size_hint=100_000_000,
             progress=None):
    """Measure downlink throughput with parallel HTTP streams.

    A single TCP stream badly underestimates LTE/5G, so several run at once.
    Returns Mbps plus the bytes actually consumed.
    """
    counter, lock = [0], threading.Lock()
    url = url_tpl.format(bytes=size_hint)
    t0 = time.monotonic()
    deadline = t0 + duration
    threads = [threading.Thread(target=_stream_down, args=(url, deadline, counter, lock),
                                daemon=True) for _ in range(max(1, streams))]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline + 2:
        if progress:
            with lock:
                got = counter[0]
            el = max(1e-3, time.monotonic() - t0)
            progress(min(1.0, el / duration), got * 8.0 / el / 1e6, got)
        time.sleep(0.2)
    for t in threads:
        t.join(timeout=3.0)
    elapsed = max(1e-3, time.monotonic() - t0)
    total = counter[0]
    return Result(direction="down", bytes=total, seconds=elapsed,
                  mbps=total * 8.0 / elapsed / 1e6, streams=streams,
                  ok=total > 0)


def _stream_up(url, payload, deadline, counter, lock):
    try:
        while time.monotonic() < deadline:
            req = urllib.request.Request(url, data=payload,
                                         headers={"User-Agent": UA,
                                                  "Content-Type": "application/octet-stream"})
            with _opener().open(req, timeout=20.0) as resp:
                resp.read(1024)
            with lock:
                counter[0] += len(payload)
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
        pass


def upload(duration=8.0, streams=3, url=DEFAULT_UP, block=2_000_000, progress=None):
    """Measure uplink throughput by POSTing incompressible blocks."""
    payload = bytes(bytearray(range(256)) * (block // 256 + 1))[:block]
    counter, lock = [0], threading.Lock()
    t0 = time.monotonic()
    deadline = t0 + duration
    threads = [threading.Thread(target=_stream_up, args=(url, payload, deadline, counter, lock),
                                daemon=True) for _ in range(max(1, streams))]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline + 5:
        if progress:
            with lock:
                got = counter[0]
            el = max(1e-3, time.monotonic() - t0)
            progress(min(1.0, el / duration), got * 8.0 / el / 1e6, got)
        time.sleep(0.2)
    for t in threads:
        t.join(timeout=5.0)
    elapsed = max(1e-3, time.monotonic() - t0)
    total = counter[0]
    return Result(direction="up", bytes=total, seconds=elapsed,
                  mbps=total * 8.0 / elapsed / 1e6, streams=streams, ok=total > 0)


def dns_lookup(host="www.google.com", timeout=3.0):
    """DNS resolution time - a good early warning for a sick data bearer."""
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    t0 = time.perf_counter()
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ms = (time.perf_counter() - t0) * 1000.0
        return Result(host=host, ms=ms, ok=True)
    except OSError as e:
        return Result(host=host, ms=None, ok=False, error=str(e))
    finally:
        socket.setdefaulttimeout(old)


def fmt_bytes(n):
    if n is None:
        return "--"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%.1f GB" % n


def fmt_mbps(v):
    if v is None:
        return "--"
    if v >= 100:
        return "%.0f Mbps" % v
    if v >= 10:
        return "%.1f Mbps" % v
    return "%.2f Mbps" % v
