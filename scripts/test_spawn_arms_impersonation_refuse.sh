#!/usr/bin/env bash
# Verifies the seat launch prefix carries IMPERSONATION_REFUSE, defaults ON, honours override.
set -uo pipefail
S="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/spawn-agent.sh"
fails=0
chk() { if eval "$2"; then echo "  ok: $1"; else echo "  FAIL: $1"; fails=$((fails+1)); fi; }

# Reproduce the prefix construction exactly as spawn-agent.sh builds it.
mk() { (
  ORCHESTRA_DIR=/d SCRIPT_DIR=/r
  [[ -n "${1:-}" ]] && export IMPERSONATION_REFUSE="$1"
  p="$(printf 'ORCHESTRA_DIR=%q ORCH_DIR=%q ORCHESTRA_ROOT=%q' "$ORCHESTRA_DIR" "$ORCHESTRA_DIR" "$SCRIPT_DIR")"
  p="$p $(printf 'IMPERSONATION_REFUSE=%q' "${IMPERSONATION_REFUSE:-1}")"
  echo "$p" ) }

echo "prefix construction:"
chk "defaults to 1 when unset"      '[[ "$(mk)"  == *"IMPERSONATION_REFUSE=1"* ]]'
chk "honours an explicit 0"         '[[ "$(mk 0)" == *"IMPERSONATION_REFUSE=0"* ]]'
chk "keeps the existing vars"       '[[ "$(mk)"  == *"ORCHESTRA_DIR=/d"* && "$(mk)" == *"ORCHESTRA_ROOT=/r"* ]]'

echo "source file:"
chk "spawn-agent.sh adds it to _orch_prefix" \
    'grep -q "IMPERSONATION_REFUSE=%q" '"$S"
# Both line numbers must EXIST as well as be ordered — an absent line must not pass
# this check by comparing empty strings (it did, until the mutation test caught it).
ord_ok() {
  local a b
  a=$(grep -n "IMPERSONATION_REFUSE=%q" "$S" | head -1 | cut -d: -f1)
  b=$(grep -n 'launch_cmd="\$_orch_prefix \$launch_cmd"' "$S" | head -1 | cut -d: -f1)
  [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]
}
chk "added BEFORE launch_cmd is assembled" 'ord_ok'
chk "bash syntax still valid" 'bash -n '"$S"

[[ $fails -eq 0 ]] && { echo "PASS"; exit 0; } || { echo "FAILED: $fails"; exit 1; }
