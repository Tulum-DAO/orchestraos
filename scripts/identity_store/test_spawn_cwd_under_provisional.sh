#!/usr/bin/env bash
# Spawn-path cwd projection-gap (gm msg_cefd4b3f, BG real-fire wall): when bg_arm's
# project_now has just PROJECTED the provisional alias into registry.json with cwd=null,
# get_agent_field is flat-FIRST and returns 'None'/'' for cwd -> short-circuits BEFORE the
# DB-first [C1] resolver -> spawn-agent.sh:369 'Working directory does not exist' -> fail-closed.
# Fix (gm option a): a null/empty flat field falls through to the DB [C1] resolve-chain.
# Flag-off stays byte-identical. Run: bash test_spawn_cwd_under_provisional.sh (exit 0 = pass)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
FAILS=0
pass() { echo "PASS — $1"; }
fail() { echo "FAIL — $1"; FAILS=$((FAILS+1)); }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/state"

# DB: canonical root1 (real cwd in lineage) + provisional root1-g2 (alias cwd=null).
REPO="$REPO" python3 - "$WORK" <<'PY'
import json, os, sys
work=sys.argv[1]; sys.path.insert(0, os.environ["REPO"])
from scripts.identity_store import orchestra_db
dbp=os.path.join(work,"state","orchestra-registry.db"); orchestra_db.init_db(dbp)
c=orchestra_db.get_connection(dbp)
try:
    c.execute("INSERT INTO lineages(root,tier,runtime,always_on,machine,cwd) VALUES('root1','T2','claude',1,'vps',?)",(work,))  # real lineage cwd
    c.execute("INSERT INTO generations(root,generation,session_id,model) VALUES('root1',1,'s1','claude-opus-4-8[1m]')")
    g1=c.execute("SELECT id FROM generations WHERE generation=1").fetchone()["id"]
    c.execute("INSERT INTO canonical(root,generation_id,tmux_session,status) VALUES('root1',?,'root1','online')",(g1,))
    c.execute("INSERT INTO source_records(file,kind,key,ordinal,payload_json) VALUES('registry.json','agent','root1',0,?)",
              (json.dumps({"name":"root1","lineage_root":"root1","tmux_session":"root1","cwd":work}),))
    c.execute("INSERT INTO generations(root,generation,session_id,model) VALUES('root1',2,'s2','claude-opus-4-8[1m]')")  # -> provisional root1-g2
finally:
    c.close()
# THE BUG CONDITION: project_now has just written the provisional g2 into FLAT registry.json
# with cwd=null (the DP-B2 alias payload's null cwd), tmux_session set.
reg={"version":1,"agents":{
    "root1":{"name":"root1","tmux_session":"root1","tier":"T2","cwd":work,"machine":"vps"},
    "root1-g2":{"name":"root1-g2","tmux_session":"root1-g2","tier":"T2","machine":"vps",
                "cwd":None,"lineage_root":"root1","generation":2,"status":"provisioning"}}}
open(os.path.join(work,"registry.json"),"w").write(json.dumps(reg))
PY

source "$REPO/spawn-agent.sh"
SCRIPT_DIR="$REPO"; REGISTRY="$WORK/registry.json"

# ---- FLAG OFF: byte-identical original behavior (null cwd -> literal 'None') ----
unset IDENTITY_STORE_CUTOVER; rm -f "$WORK/state/identity-store-cutover.flag"
off_cwd="$(get_agent_field root1-g2 cwd || true)"
[[ "$off_cwd" == "None" ]] && pass "flag-off byte-identical: null cwd still prints 'None' (legacy)" \
    || fail "flag-off changed: expected 'None', got '$off_cwd'"

# ---- ARM cutover ----
echo armed > "$WORK/state/identity-store-cutover.flag"

# THE ACCEPTANCE (gm): provisional g2 IN FLAT with cwd=null -> cwd must resolve to a REAL dir via DB [C1]
cwd="$(get_agent_field root1-g2 cwd || true)"
if [[ -n "$cwd" && "$cwd" != "None" && -d "$cwd" ]]; then
    pass "cwd-under-provisional: null-in-flat cwd resolves to a real dir via DB [C1] ($cwd)"
else
    fail "cwd-under-provisional: expected a real dir, got '$cwd' (the projection-gap wall)"
fi

# tmux_session (non-empty in flat) still uses the flat fast path
[[ "$(get_agent_field root1-g2 tmux_session)" == "root1-g2" ]] && pass "tmux_session flat fast-path intact" || fail "tmux_session"

# a normal agent with a real flat cwd is untouched (fast path, no DB needed)
[[ "$(get_agent_field root1 cwd)" == "$WORK" ]] && pass "normal agent real flat cwd untouched" || fail "normal cwd"

# spawn-agent.sh:369 would pass now (the resolved cwd is a real dir)
[[ -d "$cwd" ]] && pass "spawn-agent.sh:369 [[ -d cwd ]] passes" || fail "369 would still fail on '$cwd'"

echo "----"
[[ "$FAILS" -eq 0 ]] && { echo "ALL PASS"; exit 0; } || { echo "$FAILS FAIL"; exit 1; }
