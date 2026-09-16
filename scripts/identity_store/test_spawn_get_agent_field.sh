#!/usr/bin/env bash
# Spawn-path DB-first (Piece B) — spawn-agent.sh get_agent_field resolves a flat-flapped
# canonical/provisional agent from the identity DB under cutover, INERT when flag off.
# Sources spawn-agent.sh (its source-guard prevents main), overrides SCRIPT_DIR/REGISTRY
# to a fixture, and asserts by effect. Run: bash test_spawn_get_agent_field.sh (exit 0 = pass)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
FAILS=0
pass() { echo "PASS — $1"; }
fail() { echo "FAIL — $1"; FAILS=$((FAILS+1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/state"

# --- seed: DB has canonical root1 + provisional root1-g2 + retired root1-gen0; the flat
# registry.json has ONLY root1 (root1-g2 is FLAPPED OUT, the bug) ---
REPO="$REPO" python3 - "$WORK" <<'PY'
import json, os, sys
work = sys.argv[1]
sys.path.insert(0, os.environ["REPO"])
from scripts.identity_store import orchestra_db
dbp = os.path.join(work, "state", "orchestra-registry.db")
orchestra_db.init_db(dbp)
c = orchestra_db.get_connection(dbp)
try:
    c.execute("INSERT INTO lineages(root,tier,runtime,always_on,machine,cwd) "
              "VALUES('root1','T2','claude',1,'vps',NULL)")
    c.execute("INSERT INTO generations(root,generation,session_id,model) "
              "VALUES('root1',1,'sid1','claude-opus-4-8[1m]')")
    g1 = c.execute("SELECT id FROM generations WHERE root='root1' AND generation=1").fetchone()["id"]
    c.execute("INSERT INTO canonical(root,generation_id,tmux_session,status) "
              "VALUES('root1',?,'root1','online')", (g1,))
    doc = {"name":"root1","lineage_root":"root1","tmux_session":"root1","tier":"T2",
           "runtime":"claude","model":"claude-opus-4-8[1m]","cwd":work,"system_prompt":None}
    c.execute("INSERT INTO source_records(file,kind,key,ordinal,payload_json) "
              "VALUES('registry.json','agent','root1',0,?)", (json.dumps(doc),))
    c.execute("INSERT INTO generations(root,generation,session_id,model) "
              "VALUES('root1',2,'sid2','claude-opus-4-8[1m]')")   # -> provisional root1-g2
    c.execute("INSERT INTO generations(root,generation,session_id,model,retired_at) "
              "VALUES('root1',0,'sid0','claude-opus-4-8[1m]','2026-09-01T00:00:00Z')")  # -> root1-gen0 retired
finally:
    c.close()
# flat registry.json: ONLY root1 (root1-g2 flapped OUT)
reg = {"version":1,"agents":{"root1":{"name":"root1","tmux_session":"root1","tier":"T2",
       "cwd":work,"machine":"vps","model":"claude-opus-4-8[1m]"}}}
open(os.path.join(work,"registry.json"),"w").write(json.dumps(reg))
PY

# source spawn-agent.sh, then point it at the fixture
source "$REPO/spawn-agent.sh"
SCRIPT_DIR="$REPO"
REGISTRY="$WORK/registry.json"

# ---- INERT: cutover OFF -> flapped alias is NOT resolvable (exit 1), byte-identical ----
unset IDENTITY_STORE_CUTOVER
rm -f "$WORK/state/identity-store-cutover.flag"
if out=$(get_agent_field "root1-g2" "tmux_session"); then
    fail "RS4 INERT: flag-off must NOT resolve the flapped alias (got '$out')"
else
    pass "RS4 INERT: flag-off returns exit 1 for the flapped alias (no DB touch)"
fi
# flat present agent still resolves flat (byte-identical) under flag-off
[[ "$(get_agent_field root1 tmux_session)" == "root1" ]] && pass "flag-off flat path intact" || fail "flag-off flat path"

# ---- ARM cutover ----
echo armed > "$WORK/state/identity-store-cutover.flag"

# RS1 (= gm acceptance): flapped provisional alias resolves from the DB
got="$(get_agent_field root1-g2 tmux_session || true)"
[[ "$got" == "root1-g2" ]] && pass "RS1: flapped root1-g2 resolves tmux_session from DB" || fail "RS1: got '$got'"

# RS8 (C1): cwd resolves to a REAL existing dir (spawn-agent.sh:369 would else exit 1)
cwd="$(get_agent_field root1-g2 cwd || true)"
[[ -n "$cwd" && -d "$cwd" ]] && pass "RS8: alias cwd is a real dir ($cwd)" || fail "RS8: cwd not a real dir ('$cwd')"

# RS9 (C4 resolve-once): model also resolves consistently from the same cached snapshot
model="$(get_agent_field root1-g2 model || true)"
[[ "$model" == "claude-opus-4-8[1m]" ]] && pass "RS9: model consistent from cache" || fail "RS9: model '$model'"

# RS7 (C2): retired archive root1-gen0 is NOT spawnable (exit 1 -> auto-register path)
if get_agent_field root1-gen0 tmux_session >/dev/null; then
    fail "RS7: retired root1-gen0 must NOT resolve"
else
    pass "RS7: retired root1-gen0 returns exit 1 (not spawnable)"
fi

# flat-present agent still uses the flat path under cutover (fast path, unchanged)
[[ "$(get_agent_field root1 tmux_session)" == "root1" ]] && pass "cutover flat fast-path intact" || fail "cutover flat path"

echo "----"
[[ "$FAILS" -eq 0 ]] && { echo "ALL PASS"; exit 0; } || { echo "$FAILS FAIL"; exit 1; }
