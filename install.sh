#!/data/data/com.termux/files/usr/bin/bash
# nettrack installer for Termux.
set -eu

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s  ok%s  %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s warn%s %s\n' "$YEL" "$RST" "$*"; }
die()  { printf '%s fail%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }

say "nettrack installer"
say "${DIM}------------------${RST}"

# 1. The installer writes into $PREFIX and drops a launcher in its bin, so it
#    has to run where that prefix exists - the native Termux shell. (The app
#    itself often works from proot, since termux-api talks to the Termux:API
#    app over a socket; only the install step needs to be here.)
if grep -qi proot /proc/version 2>/dev/null; then
  die "This is a proot-distro shell, which has no Termux \$PREFIX to install
     into. Type 'exit' to return to the native Termux shell and run it there."
fi
[ -n "${PREFIX:-}" ] && [ -d "$PREFIX" ] || die "PREFIX not set - are you in Termux?"
ok "native Termux detected"

# 2. Python
if ! command -v python3 >/dev/null 2>&1; then
  say "installing python…"; pkg install -y python
fi
ok "python $(python3 -V 2>&1 | cut -d' ' -f2)"

# 3. termux-api command line tools
if ! command -v termux-telephony-cellinfo >/dev/null 2>&1; then
  say "installing termux-api…"; pkg install -y termux-api
fi
command -v termux-telephony-cellinfo >/dev/null 2>&1 \
  && ok "termux-api tools present" \
  || die "termux-api install failed"

# 4. Copy the package
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$PREFIX/lib/nettrack"
rm -rf "$DEST"; mkdir -p "$DEST"
cp -r "$SRC/nettrack" "$DEST/"
[ -f "$SRC/cellfile.example.csv" ] && cp "$SRC/cellfile.example.csv" "$DEST/"
ok "installed to $DEST"

# 5. Launcher
cat > "$PREFIX/bin/nettrack" <<LAUNCH
#!$PREFIX/bin/sh
exec python3 -X utf8 -m nettrack "\$@"
LAUNCH
sed -i "2i export PYTHONPATH=\"$DEST:\${PYTHONPATH:-}\"" "$PREFIX/bin/nettrack"
chmod +x "$PREFIX/bin/nettrack"
ok "launcher at $PREFIX/bin/nettrack"

# 6. Shared storage for logs
if [ ! -d "$HOME/storage/shared" ]; then
  warn "shared storage not set up - run 'termux-setup-storage' so logs land in
     /sdcard/nettrack instead of the Termux private directory"
fi

say ""
say "${GRN}installed${RST}. Next steps:"
say "  1. Install the ${YEL}Termux:API app${RST} (same source as Termux - F-Droid"
say "     or GitHub). The pkg alone is not enough."
say "  2. Grant it Location permission, set to ${YEL}Allow all the time${RST}:"
say "     Settings > Apps > Termux:API > Permissions > Location"
say "     Android returns an empty cell list without it."
say "  3. Verify:   ${YEL}nettrack doctor${RST}"
say "  4. Run:      ${YEL}nettrack${RST}"
