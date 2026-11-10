#!/usr/bin/env bash
# Delete Guard Hook — PreToolUse for Bash. Exits 0 = allow, 2 = block.
# Logic lives in hooks/delete_guard.py (versioned with the checkout).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/hooks/delete_guard.py"
