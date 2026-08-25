"""A small ANSI terminal toolkit: framebuffer, widgets and raw key input.

Deliberately dependency-free and sized for a phone screen - every widget
takes an explicit width and degrades when the terminal is narrow.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import sys
import termios
import tty

CSI = "\x1b["
ALT_ON = CSI + "?1049h"
ALT_OFF = CSI + "?1049l"
CUR_HIDE = CSI + "?25l"
CUR_SHOW = CSI + "?25h"
CLEAR = CSI + "2J" + CSI + "H"
RESET = CSI + "0m"

# 256-colour palette indices
C = {
    "reset": RESET, "bold": CSI + "1m", "dim": CSI + "2m", "rev": CSI + "7m",
    "white": CSI + "38;5;255m", "grey": CSI + "38;5;245m", "dark": CSI + "38;5;238m",
    "green": CSI + "38;5;46m", "green2": CSI + "38;5;40m", "cyan": CSI + "38;5;51m",
    "yellow": CSI + "38;5;226m", "orange": CSI + "38;5;208m", "red": CSI + "38;5;196m",
    "magenta": CSI + "38;5;201m", "blue": CSI + "38;5;39m", "purple": CSI + "38;5;141m",
    "bgblue": CSI + "48;5;24m", "bgdark": CSI + "48;5;235m", "bggrey": CSI + "48;5;239m",
}

# Quality 0..4 -> palette key (paint()/hbar() resolve names, not escapes)
QCOLOR = ["magenta", "red", "yellow", "green2", "green"]
QNAME = ["No service", "Bad", "Fair", "Good", "Excellent"]

SPARKS = "▁▂▃▄▅▆▇█"
BLOCKS = "▏▎▍▌▋▊▉█"

_NOCOLOR = os.environ.get("NO_COLOR") is not None


def c(name):
    return "" if _NOCOLOR else C.get(name, "")


def paint(text, *names):
    if _NOCOLOR or not names:
        return text
    return "".join(c(n) for n in names) + text + RESET


def strip(s):
    """Length of a string ignoring ANSI escapes."""
    out, i, n = 0, 0, len(s)
    while i < n:
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out


def fit(s, width, pad=True, ellipsis="…"):
    """Truncate/pad to an exact visible width, preserving colour codes."""
    vis = strip(s)
    if vis <= width:
        return s + (" " * (width - vis) if pad else "")
    out, count, i = [], 0, 0
    while i < len(s) and count < width - 1:
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1:
                break
            out.append(s[i:j + 1])
            i = j + 1
            continue
        out.append(s[i])
        count += 1
        i += 1
    return "".join(out) + ellipsis + RESET


def term_size(default=(80, 24)):
    try:
        sz = shutil.get_terminal_size(default)
        return (max(28, sz.columns), max(10, sz.lines))
    except OSError:
        return default


# ------------------------------------------------------------------ widgets
def hbar(value, vmin, vmax, width, color=None, track="░"):
    """Horizontal gauge with sub-cell resolution."""
    if width <= 0:
        return ""
    if value is None:
        return paint(track * width, "dark")
    frac = (float(value) - vmin) / float(vmax - vmin) if vmax != vmin else 0.0
    frac = max(0.0, min(1.0, frac))
    full = int(frac * width)
    rem = frac * width - full
    bar = "█" * full
    if full < width and rem > 0.05:
        bar += BLOCKS[min(7, int(rem * 8))]
    body = paint(bar, color) if color else bar
    return body + paint(track * (width - strip(bar)), "dark")


def sparkline(values, width, color=None, vmin=None, vmax=None):
    """Unicode sparkline over the last `width` values."""
    vals = [v for v in list(values)[-width:] if v is not None]
    if not vals or width <= 0:
        return paint("·" * max(0, width), "dark")
    lo = vmin if vmin is not None else min(vals)
    hi = vmax if vmax is not None else max(vals)
    if hi == lo:
        hi = lo + 1.0
    out = []
    for v in list(values)[-width:]:
        if v is None:
            # re-assert the series colour after the gap's own reset
            out.append(paint("·", "dark") + (c(color) if color else ""))
        else:
            idx = int((max(lo, min(hi, v)) - lo) / (hi - lo) * (len(SPARKS) - 1))
            out.append(SPARKS[idx])
    s = "".join(out)
    return paint(s, color) if color and not _NOCOLOR else s


def rule(width, title=None, style="dark"):
    if not title:
        return paint("─" * width, style)
    t = " %s " % title
    left = 2
    right = max(0, width - left - len(t))
    return paint("─" * left, style) + paint(t, "bold", "white") + paint("─" * right, style)


def kv(label, value, width, lw=None, vcolor=None):
    """`label ....... value` line."""
    lw = lw or min(14, max(8, width // 3))
    lab = fit(paint(label, "grey"), lw)
    val = paint(str(value), vcolor) if vcolor else str(value)
    return lab + fit(val, max(0, width - lw))


def table(headers, rows, widths, aligns=None, header_style=("bold", "white")):
    """Render a fixed-width table as a list of lines."""
    aligns = aligns or ["<"] * len(headers)
    # A caller may zero a column's width to drop it on a narrow screen.
    keep = [i for i, w in enumerate(widths) if w > 0]
    headers = [headers[i] for i in keep]
    widths = [widths[i] for i in keep]
    aligns = [aligns[i] for i in keep]
    rows = [[r[i] for i in keep if i < len(r)] for r in rows]
    out = []
    cells = []
    for h, w, a in zip(headers, widths, aligns):
        cells.append(fit(paint(("%*s" % (w, h)) if a == ">" else h, *header_style), w))
    out.append(" ".join(cells))
    for row in rows:
        cells = []
        for val, w, a in zip(row, widths, aligns):
            s = str(val)
            if a == ">":
                pad = w - strip(s)
                s = (" " * pad + s) if pad > 0 else s
            cells.append(fit(s, w))
        out.append(" ".join(cells))
    return out


def badge(text, fg="white", bg="bgblue"):
    if _NOCOLOR:
        return "[%s]" % text
    return c(bg) + c(fg) + " " + text + " " + RESET


# --------------------------------------------------------------- framebuffer
class Screen:
    """Double-buffered full-screen renderer with flicker-free diff updates."""

    def __init__(self, stream=None):
        self.out = stream or sys.stdout
        self.w, self.h = term_size()
        self._prev = []
        self._resized = True
        try:
            signal.signal(signal.SIGWINCH, self._on_resize)
        except (ValueError, AttributeError, OSError):
            pass

    def _on_resize(self, *_a):
        self._resized = True

    def poll_resize(self):
        if self._resized:
            self.w, self.h = term_size()
            self._prev = []
            self._resized = False
            self.out.write(CLEAR)
            return True
        return False

    def enter(self):
        self.out.write(ALT_ON + CUR_HIDE + CLEAR)
        self.out.flush()

    def leave(self):
        self.out.write(RESET + CUR_SHOW + ALT_OFF)
        self.out.flush()

    def draw(self, lines):
        """Write only the rows that changed since the previous frame."""
        lines = [fit(l, self.w) for l in lines[:self.h]]
        while len(lines) < self.h:
            lines.append(" " * self.w)
        buf = []
        for i, line in enumerate(lines):
            if i >= len(self._prev) or self._prev[i] != line:
                buf.append("%s%d;1H%s%s" % (CSI, i + 1, line, RESET))
        if buf:
            self.out.write("".join(buf))
            self.out.flush()
        self._prev = lines

    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, *exc):
        self.leave()
        return False


class Keys:
    """Non-blocking single-key reader in cbreak mode."""

    def __init__(self, stream=None):
        self.fh = stream or sys.stdin
        self.fd = self.fh.fileno()
        self.saved = None
        self.enabled = self.fh.isatty()

    def __enter__(self):
        if self.enabled:
            try:
                self.saved = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)
            except termios.error:
                self.enabled = False
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            except termios.error:
                pass
        return False

    def get(self, timeout=0.0):
        """Return a key name or None. Arrow keys map to up/down/left/right."""
        if not self.enabled:
            return None
        r, _w, _e = select.select([self.fh], [], [], timeout)
        if not r:
            return None
        ch = self.fh.read(1)
        if ch != "\x1b":
            return ch
        r, _w, _e = select.select([self.fh], [], [], 0.05)
        if not r:
            return "esc"
        seq = self.fh.read(1)
        if seq != "[":
            return "esc"
        r, _w, _e = select.select([self.fh], [], [], 0.05)
        if not r:
            return "esc"
        code = self.fh.read(1)
        return {"A": "up", "B": "down", "C": "right", "D": "left",
                "H": "home", "F": "end"}.get(code, "esc")
