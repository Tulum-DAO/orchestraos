"""Layer A (Bug 2, live-but-unreachable pane states): spawn-time permission
allow-rules for claude seats.

WHY: even under --dangerously-skip-permissions the Claude harness still raises
an interactive permission prompt for operations it flags sensitive (class-2
live-but-unreachable: arkdata-mcp-artifact-spec froze ~1h on a benign
memory-index sed). A headless seat can never answer that prompt, so its pane is
ALIVE yet unreachable for mail. Pre-configuring an explicit allow-set for the
seat's own project-local writes prevents the benign-self-edit prompt class at
the source.

gm RULING 1 (BINDING, .workspace/proposals/live-but-unreachable-pane-states-
design.md): the allow-set is STRICTLY cwd/project-local + own-state/.workspace
WRITES. Nothing blanket — no shell (Bash), no network (WebFetch/WebSearch), no
cross-tenant paths. Anything outside this set still prompts -> escalates via
Layer B (pane_reachability). NEVER widen these rules.

Consumed by spawn-agent.sh (claude runtime branch) via:
    python3 scripts/spawn_permission_rules.py <agent-id> <cwd>
which writes /tmp/agent-perms-<id>.settings.json (same convention as the
/tmp/agent-init-<id>.md files) and prints its path for a `--settings` flag.

Rule syntax: Claude Code permission rules, absolute-path form `Tool(//abs/**)`.
"""
import argparse
import json
import os

# Write-capable tools only — the benign-self-edit class. A shell/network tool
# in this list would be a blanket grant (gm ruling 1 violation).
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

DEFAULT_ORCHESTRA_DIR = os.path.expanduser("~/scripts/agent-orchestra")


def _norm(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p)).rstrip("/")


def build_allow_rules(cwd: str, orchestra_dir: str | None = None) -> list[str]:
    """The ONE allow-set: seat cwd + orchestra own-state + .workspace, writes
    only. Deterministic order; deduped when cwd == orchestra_dir subsumes."""
    cwd = _norm(cwd)
    orch = _norm(orchestra_dir or DEFAULT_ORCHESTRA_DIR)
    scopes = [cwd]
    for extra in (orch + "/.workspace", orch + "/state"):
        # cwd/** already covers orchestra subdirs when the seat lives in the
        # orchestra checkout — don't emit redundant rules.
        if not (extra + "/").startswith(cwd + "/"):
            scopes.append(extra)
    return [f"{tool}(//{scope.lstrip('/')}/**)"
            for scope in scopes for tool in WRITE_TOOLS]


def path_covered(rules: list[str], tool: str, path: str) -> bool:
    """True iff `tool` writing `path` falls inside the generated allow-set.
    Mirrors the harness's absolute `Tool(//prefix/**)` matching for OUR rule
    shape only — used to prove cross-tenant/shell paths stay UNcovered."""
    path = _norm(path)
    for rule in rules:
        prefix = f"{tool}(//"
        if not (rule.startswith(prefix) and rule.endswith("/**)")):
            continue
        scope = "/" + rule[len(prefix):-len("/**)")]
        if path == scope or path.startswith(scope + "/"):
            return True
    return False


def _bg_green_session_start_hook(orchestra_dir: str | None = None) -> dict | None:
    """BG leg-(ii) P2.7 / Design 2: when THIS spawn is a BG green (BG_GREEN_ROOT set
    in the env — spawn_green exports it only for a green), install a green-scoped
    SessionStart hook that runs capture_green_sid.py so the green writes its real
    session_id to state/wal/<alias>.sid (which M3 attributes at promote). Green-scoped
    (rides the per-seat --settings file), so NO global ~/.claude/settings.json edit.
    Returns None for a normal seat → the settings stay allow-only (legacy-identical)."""
    if not os.environ.get("BG_GREEN_ROOT"):
        return None
    orch = _norm(orchestra_dir or DEFAULT_ORCHESTRA_DIR)
    script = os.path.join(orch, "scripts", "lineage_daemon", "wal", "capture_green_sid.py")
    return {"SessionStart": [
        {"hooks": [{"type": "command", "command": f"python3 {script}", "timeout": 5}]}
    ]}


def write_settings(agent_id: str, cwd: str, orchestra_dir: str | None = None,
                   out_dir: str = "/tmp") -> str:
    """Write the per-seat settings file ({"permissions":{"allow":[...]}}) and
    return its path. Allow-only for a normal seat: no deny/ask keys, nothing else
    overridden. For a BG green (BG_GREEN_ROOT set) it ALSO carries a green-scoped
    SessionStart hook (capture_green_sid.py) — see _bg_green_session_start_hook."""
    rules = build_allow_rules(cwd, orchestra_dir=orchestra_dir)
    settings: dict = {"permissions": {"allow": rules}}
    bg_hooks = _bg_green_session_start_hook(orchestra_dir=orchestra_dir)
    if bg_hooks:
        settings["hooks"] = bg_hooks
    path = os.path.join(out_dir, f"agent-perms-{agent_id}.settings.json")
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("agent_id")
    ap.add_argument("cwd")
    ap.add_argument("--orchestra-dir", default=None)
    ap.add_argument("--out-dir", default="/tmp")
    args = ap.parse_args()
    print(write_settings(args.agent_id, args.cwd,
                         orchestra_dir=args.orchestra_dir,
                         out_dir=args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
