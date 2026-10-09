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
mkdir -p "$DIR" 2>/dev/null && chmod 700 "$DIR" 2>/dev/null

# stdin to a file, not a variable: $(...) strips trailing newlines, and a chained command must
# receive the bytes Claude Code sent.
IN="$DIR/in.$$"
cat > "$IN" 2>/dev/null || IN=/dev/null

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

"${ORCHESTRA_PY:-python3}" "$HERE/statusline.py" "$@" < "$IN" > "$OUT.$$" 2>/dev/null
if [ -s "$OUT.$$" ]; then
    mv -f "$OUT.$$" "$OUT" 2>/dev/null && cat "$OUT" || cat "$OUT.$$"
else
    rm -f "$OUT.$$" 2>/dev/null
    echo "Claude"
fi
rm -f "$IN" 2>/dev/null
exit 0
