"""RED-first tests for bg_quarantine.py — BG Layer-4 prewarm quarantine.

Congruence DEC-1788660122 (rotation-autonomy-builder-claude + -agy APPROVE). The re-run gap: a
PREWARMED green booted alive and ran as a PARALLEL LIVE WRITER alongside blue (mutated state, fired a
the operator AskUserQuestion) while blue was still canonical. Layer-4 holds the prewarmed green READ-ONLY until
swap-wake, then it SOLE-continues via the Layer-3 capsule.

Mechanism M2 (env-scoped PreToolUse deny-hook): the guard is a LINE-1 no-op for any session WITHOUT
the green's BG_QUARANTINE_ALIAS env (blast-radius contained by construction — it can NEVER block blue or
any of the ~40 live agents). Only for a green with that env + an ACTIVE marker does it enforce a strict
READ-ONLY ALLOW-LIST (AGY hardening: allow-list, not a mutating blacklist — Bash heuristics are
escape-prone). Swap-wake removes the marker to lift quarantine without respawn. Fail-safe: allow-on-error
for unset env (fleet safety); deny-on-error for a known green (never let a shadow mutate).
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_quarantine as q  # noqa: E402


# ── blast-radius containment: the guard is a strict no-op without the green env ──

def test_no_env_allows_every_tool_including_mutating(tmp_path):
    """The load-bearing safety property: a session WITHOUT BG_QUARANTINE_ALIAS is untouched — every
    tool (incl. Write/Bash/Agent) is allowed. This is what makes the guard safe for ~40 live agents."""
    for tool in ("Write", "Edit", "Bash", "Agent", "AskUserQuestion", "Read"):
        allow, _ = q.decide(tool, env={}, wal_dir=str(tmp_path))
        assert allow is True, f"{tool} must be allowed for a non-quarantined session"


# ── an active quarantine green: read-only ALLOW-LIST, deny everything else ──

def test_active_quarantine_denies_mutating_and_outward_tools(tmp_path):
    alias = "second-brain-dev-g5"
    q.arm_quarantine(str(tmp_path), alias)
    env = {q.ENV_KEY: alias}
    for tool in ("Write", "Edit", "NotebookEdit", "Bash", "Agent", "Task",
                 "AskUserQuestion", "mcp__anything__do"):
        allow, _ = q.decide(tool, env=env, wal_dir=str(tmp_path))
        assert allow is False, f"{tool} must be DENIED for an active-quarantine green"


def test_active_quarantine_allows_read_only_tools(tmp_path):
    alias = "second-brain-dev-g5"
    q.arm_quarantine(str(tmp_path), alias)
    env = {q.ENV_KEY: alias}
    for tool in ("Read", "Grep", "Glob"):
        allow, _ = q.decide(tool, env=env, wal_dir=str(tmp_path))
        assert allow is True, f"{tool} (read-only) must be allowed under quarantine"


# ── swap-wake lifts quarantine (no respawn): the promoted green acts ──

def test_lifted_quarantine_allows_mutation(tmp_path):
    alias = "second-brain-dev-g5"
    q.arm_quarantine(str(tmp_path), alias)
    assert q.is_quarantined(str(tmp_path), alias) is True
    q.lift_quarantine(str(tmp_path), alias)             # swap-wake effect
    assert q.is_quarantined(str(tmp_path), alias) is False
    env = {q.ENV_KEY: alias}
    allow, _ = q.decide("Write", env=env, wal_dir=str(tmp_path))
    assert allow is True, "a promoted (unquarantined) green must be able to mutate"


def test_arm_then_lift_is_idempotent(tmp_path):
    alias = "g"
    q.arm_quarantine(str(tmp_path), alias)
    q.arm_quarantine(str(tmp_path), alias)              # re-arm no-op
    assert q.is_quarantined(str(tmp_path), alias) is True
    assert q.lift_quarantine(str(tmp_path), alias) is True
    assert q.lift_quarantine(str(tmp_path), alias) is False   # already gone


# ── fail-safe direction + hardening ──

def test_alias_traversal_is_rejected(tmp_path):
    """A malicious/mangled BG_QUARANTINE_ALIAS must not escape the quarantine dir."""
    env = {q.ENV_KEY: "../../etc/passwd"}
    allow, reason = q.decide("Write", env=env, wal_dir=str(tmp_path))
    assert allow is False           # env set + unsafe alias -> deny (known green context, fail-closed)
    assert "unsafe" in reason.lower() or "deny" in reason.lower()


def test_error_fail_closed_only_when_env_set(tmp_path):
    """deny-on-error for a known green; a broken wal_dir with env SET denies (never let a shadow act).
    With env UNSET, the guard exits before any I/O -> always allow (fleet safety)."""
    alias = "g"
    q.arm_quarantine(str(tmp_path), alias)
    # env set but wal_dir is a FILE (marker read raises) -> deny
    bad = tmp_path / "notadir"
    bad.write_text("x")
    allow, _ = q.decide("Write", env={q.ENV_KEY: alias}, wal_dir=str(bad / "sub"))
    assert allow is False
    # env unset + same bad dir -> allow (never enters the guarded path)
    allow2, _ = q.decide("Write", env={}, wal_dir=str(bad / "sub"))
    assert allow2 is True


# ── the PreToolUse hook protocol: exit 0 = allow, exit 2 = block ──

def test_main_exits_0_when_no_env(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.delenv(q.ENV_KEY, raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name":"Write","tool_input":{}}'))
    assert q.main() == 0


def test_main_exits_2_when_active_quarantine_blocks_mutation(tmp_path, monkeypatch):
    import io
    alias = "g"
    q.arm_quarantine(str(tmp_path), alias)
    monkeypatch.setenv(q.ENV_KEY, alias)
    monkeypatch.setenv(q.WALDIR_ENV, str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name":"Write","tool_input":{"file_path":"x"}}'))
    assert q.main() == 2


def test_main_exits_0_for_read_under_quarantine(tmp_path, monkeypatch):
    import io
    alias = "g"
    q.arm_quarantine(str(tmp_path), alias)
    monkeypatch.setenv(q.ENV_KEY, alias)
    monkeypatch.setenv(q.WALDIR_ENV, str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name":"Read","tool_input":{"file_path":"x"}}'))
    assert q.main() == 0
