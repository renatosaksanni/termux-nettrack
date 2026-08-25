#!/usr/bin/env python3
"""Pre-commit checks for nettrack.

`compileall` only proves the syntax parses; it happily accepts a name that
was never imported. This adds the two checks that catch that class of bug:
a scan for undefined names, and a smoke run of every subcommand.

    python3 scripts/check.py
"""

import ast
import builtins
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "nettrack"
BUILTINS = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__package__"}


def bound_names(tree):
    """Everything a module binds: imports, defs, assignments, arguments."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def check_names():
    problems = []
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        known = bound_names(tree) | BUILTINS
        seen = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in known and node.id not in seen:
                    seen[node.id] = node.lineno
        for name, line in sorted(seen.items(), key=lambda kv: kv[1]):
            problems.append("%s:%d undefined name %r" % (path.name, line, name))
    return problems


COMMANDS = [
    ["--help"],
    ["doctor"],
    ["cells", "--sim"],
    ["cells", "--sim", "--json"],
    ["band", "1650", "632628"],
    ["adb", "status"],
    ["adb", "setup"],
    ["adb", "pair", "12345", "000000"],
]


def smoke():
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        runs = COMMANDS + [["log", "--sim", "-i", "0.5", "--duration", "2",
                            "--logdir", tmp]]
        for argv in runs:
            p = subprocess.run([sys.executable, "-m", "nettrack"] + argv,
                               cwd=str(ROOT), capture_output=True, text=True,
                               timeout=120, stdin=subprocess.DEVNULL)
            out = p.stdout + p.stderr
            label = " ".join(argv)
            if "Traceback" in out:
                first = next((l for l in out.splitlines() if "Error" in l), "traceback")
                problems.append("%s -> %s" % (label, first.strip()))
            else:
                print("  ok   nettrack %s" % label)
    return problems


def check_preflight():
    """The start-up gate must judge the backend actually selected.

    A stubbed termux-api once blocked start-up even when the adb backend was
    connected and serving, while `doctor` reported everything was ready.
    """
    sys.path.insert(0, str(ROOT))
    from nettrack import adbsrc, cli

    parser, _common = cli.build_parser()
    args = parser.parse_args(["monitor", "--source", "adb"])

    class Connected(adbsrc.AdbSource):
        def status(self):
            return (True, "127.0.0.1:1")

    class Offline(adbsrc.AdbSource):
        def status(self):
            return (False, "no adb device")

    problems = []
    if not cli._preflight(args, Connected()):
        problems.append("preflight blocks start-up with a connected adb backend")
    if cli._preflight(args, Offline()):
        problems.append("preflight allows start-up with no adb backend")
    if not problems:
        print("  ok   preflight follows the selected backend")
    return problems


def main():
    print("undefined names:")
    bad = check_names()
    for b in bad:
        print("  FAIL " + b)
    if not bad:
        print("  none")
    print("preflight:")
    bad += check_preflight()
    print("smoke run:")
    bad += smoke()
    if bad:
        print("\n%d problem(s)" % len(bad))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
