"""Start-up banner.

Three sizes so the logo survives a folded phone screen: a full block
wordmark, a compact line-drawing wordmark, and a single-line fallback.
"""

from __future__ import annotations

from . import __version__
from .ui import RESET, c, fit, paint, strip

AUTHOR = "Saksanni"
TAGLINE = "cellular network monitor"

# ANSI Shadow wordmark, 68 columns.
BIG = [
    "███╗   ██╗███████╗████████╗████████╗██████╗  █████╗  ██████╗██╗  ██╗",
    "████╗  ██║██╔════╝╚══██╔══╝╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██║ ██╔╝",
    "██╔██╗ ██║█████╗     ██║      ██║   ██████╔╝███████║██║     █████╔╝ ",
    "██║╚██╗██║██╔══╝     ██║      ██║   ██╔══██╗██╔══██║██║     ██╔═██╗ ",
    "██║ ╚████║███████╗   ██║      ██║   ██║  ██║██║  ██║╚██████╗██║  ██╗",
    "╚═╝  ╚═══╝╚══════╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝",
]

# Calvin S wordmark, 24 columns.
SMALL = [
    "┌┐┌┌─┐┌┬┐┌┬┐┬─┐┌─┐┌─┐┬┌─",
    "│││├┤  │  │ ├┬┘├─┤│  ├┴┐",
    "┘└┘└─┘ ┴  ┴ ┴└─┴ ┴└─┘┴ ┴",
]

# Vertical gradient, brightest in the middle - reads as a lit silhouette.
GRAD_BIG = ["blue", "cyan", "white", "white", "cyan", "blue"]
GRAD_SMALL = ["cyan", "white", "blue"]


def _sign(width, indent=1):
    """`v1.0.0 · by Saksanni`, right-aligned when there is room."""
    return "v%s %s by %s" % (__version__, "·", AUTHOR)


def render(width=80, subtitle=None):
    """Return the banner as a list of lines sized for `width`."""
    sub = subtitle or TAGLINE
    sign = _sign(width)

    if width >= len(BIG[0]) + 2:
        art, grad = BIG, GRAD_BIG
    elif width >= len(SMALL[0]) + 2:
        art, grad = SMALL, GRAD_SMALL
    else:
        return [paint(" nettrack", "bold", "cyan") + paint("  " + sign, "dark")]

    aw = len(art[0])
    # Side-by-side when the terminal is wide enough, stacked otherwise.
    side = width >= aw + max(len(sub), len(sign)) + 5
    out = []
    for i, row in enumerate(art):
        line = " " + paint(row, grad[i % len(grad)])
        if side:
            gap = " " * 3
            if i == len(art) - 2:
                line += gap + paint(sub, "grey")
            elif i == len(art) - 1:
                line += gap + paint(sign, "dark")
        out.append(line)
    if not side:
        out.append(" " + paint(sub, "grey"))
        out.append(" " + paint(sign, "dark"))
    return out


def show(width=80, subtitle=None, stream=None):
    """Print the banner."""
    import sys
    stream = stream or sys.stdout
    for line in render(width, subtitle):
        stream.write(line + "\n")
    stream.flush()


def term_width(default=80):
    from .ui import term_size
    return term_size((default, 24))[0]
