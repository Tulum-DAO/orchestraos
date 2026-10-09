#!/bin/sh
# statusline.sh — cache in front of hooks/statusline.py (the OrchestraOS Claude Code status line).
#
# Claude Code runs the statusLine command on every redraw, several times a second while a seat
# streams. Starting Python each time costs ~25 ms of CPU. This shim serves the last output for the
# same session for CACHE_S seconds and runs statusline.py only when that is stale, so the context
# reading it writes is at most CACHE_S old (readers allow 60 s; scripts/context_reading.py).
#
# Usage: statusline.sh [CHAIN]   (CHAIN is passed through to statusline.py unchanged)
# Env:   ORCHESTRA_PY  the python3 to run (the installer bakes it in); default python3.
# Always exits 0: a status line must never fail the redraw.

CACHE_S=30
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DIR="${TMPDIR:-/tmp}/orchestraos-statusline-$(id -u)"
PY="${ORCHESTRA_PY:-python3}"

# The cache dir sits in a shared temp dir under a predictable name. Use it only if it is a real
# directory that THIS user owns and no one else can write; otherwise another user could plant
# symlinks in it (our writes would land on their targets) or feed us cached output. Not ours ->
# no cache: run Python with stdin passed straight through.
mkdir -m 700 "$DIR" 2>/dev/null
if [ -L "$DIR" ] || [ ! -d "$DIR" ] || [ ! -O "$DIR" ] \
        || [ -n "$(find "$DIR" -maxdepth 0 -perm -g+w -o -maxdepth 0 -perm -o+w 2>/dev/null)" ]; then
    "$PY" "$HERE/statusline.py" "$@" 2>/dev/null || echo "Claude"
    exit 0
fi

# stdin to a file, not a variable: $(...) strips trailing newlines, and a chained command must
# receive the bytes Claude Code sent. mktemp: a fresh, exclusively created name.
IN=$(mktemp "$DIR/in.XXXXXX" 2>/dev/null) || IN=""
if [ -z "$IN" ]; then
    "$PY" "$HERE/statusline.py" "$@" 2>/dev/null || echo "Claude"
    exit 0
fi
cat > "$IN" 2>/dev/null

SID=$(grep -o '"session_id" *: *"[^"]*"' "$IN" 2>/dev/null | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
case "$SID" in ''|*/*) KEY=none ;; *) KEY="$SID" ;; esac
OUT="$DIR/$KEY.out"

if [ "$KEY" != none ] && [ -f "$OUT" ]; then
    MT=$(stat -c %Y "$OUT" 2>/dev/null || stat -f %m "$OUT" 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - MT )) -lt "$CACHE_S" ]; then
        cat "$OUT"
        rm -f "$IN" 2>/dev/null
        exit 0
    fi
fi

NEW=$(mktemp "$DIR/out.XXXXXX" 2>/dev/null) || NEW=""
if [ -z "$NEW" ]; then
    "$PY" "$HERE/statusline.py" "$@" < "$IN" 2>/dev/null || echo "Claude"
elif "$PY" "$HERE/statusline.py" "$@" < "$IN" > "$NEW" 2>/dev/null; then
    # by exit status, not size: a chained command may legitimately print nothing
    cat "$NEW"
    mv -f "$NEW" "$OUT" 2>/dev/null || rm -f "$NEW" 2>/dev/null
else
    rm -f "$NEW" 2>/dev/null
    echo "Claude"
fi
rm -f "$IN" 2>/dev/null
exit 0
