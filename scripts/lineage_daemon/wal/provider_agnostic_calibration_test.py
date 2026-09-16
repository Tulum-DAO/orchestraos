"""RED-first: provider-agnostic ctx calibration v2 (the operator #1; gm RULING msg_02d19242).

v2 (gm-approved) supersedes the v1 enumerated allowlist (calibrated=runtime in
{claude,codex,gemini}) which VIOLATED the operator's zero-runtime-name-in-core rule.

THE FIX (data-driven, no runtime names):
  ceiling_calibrated = (ctx present AND fresh AND 0 <= ctx <= 1)
Fail-closed: absent/stale/out-of-range ctx => uncalibrated => noop:solo-alarm, for EVERY
runtime incl claude. Death still dominates calibration for every runtime.
Ctx comes from a provider-adapter read_ctx registry (thin wrappers over existing readers:
claude detector-file, codex codex_context, gemini gemini_context), each NORMALIZED so
read_ctx returns the same "fraction of usable context, 0..1" meaning for every provider.
Thresholds stay uniform (0.70/0.80) in the core.
"""
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_beat import build_obs  # noqa: E402
from lineage_daemon.wal.decide_bg import decide_bg  # noqa: E402


def _agent(runtime, pct):
    return {"agent_id": "seat", "runtime": runtime, "death": {},
            "ctx": {"status_bar_pct": pct}}


def _blue():
    return {"generation": 1, "blue_generation_id": 10, "model": "m", "session_id": None}


# ---- data-driven calibration: ctx present+fresh+in-range => calibrated, ANY runtime ----

def test_codex_with_fresh_ctx_calibrated():
    assert build_obs(_agent("codex", 85), _blue())["ceiling_calibrated"] is True


def test_gemini_with_fresh_ctx_calibrated():
    assert build_obs(_agent("gemini", 85), _blue())["ceiling_calibrated"] is True


def test_novel_future_runtime_with_fresh_ctx_calibrated():
    """The whole point of data-driven: a runtime we have never named is calibrated the
    moment it presents a valid ctx number (its adapter provided one)."""
    assert build_obs(_agent("some-future-provider", 85), _blue())["ceiling_calibrated"] is True


def test_claude_with_fresh_ctx_calibrated():
    assert build_obs(_agent("claude", 85), _blue())["ceiling_calibrated"] is True


def test_absent_ctx_uncalibrated_failclosed_any_runtime():
    """Fail-closed is on ABSENT/stale ctx, NOT on runtime name — for every runtime."""
    for rt in ("claude", "codex", "gemini", "some-future-provider"):
        obs = build_obs(_agent(rt, None), _blue())
        assert obs["ceiling_calibrated"] is False, f"{rt} with no ctx must fail closed"


# ---- decide_bg fires by data, not runtime name ----

def test_codex_ctx_fires_swap():
    d = decide_bg(build_obs(_agent("codex", 85), _blue()))
    assert d["action"] == "swap" and d["reason"] == "ctx:swap", d


def test_gemini_ctx_prewarm_band():
    d = decide_bg(build_obs(_agent("gemini", 73), _blue()))
    assert d["action"] == "prewarm" and d["reason"] == "ctx:prewarm", d


def test_death_fires_any_runtime_even_uncalibrated():
    a = _agent("some-future-provider", None)   # no ctx -> uncalibrated
    a["death"] = {"exhausted": True}
    d = decide_bg(build_obs(a, _blue()))
    assert d["action"] == "swap" and d["reason"].startswith("death:"), d


# ---- gm-required: ZERO runtime-name literals in the rotation CORE ----

CORE_FILES = [
    "scripts/lineage_daemon/wal/decide_bg.py",
    "scripts/lineage_daemon/wal/bg_beat.py",          # build_obs lives here (calibration)
    # v2.1 addendum (gm held-condition c): every NEW core file is grep-gated too.
    "scripts/lineage_daemon/wal/bg_arm.py",           # #5 WAL-at-arm gate (the beat)
    "scripts/lineage_daemon/wal/bg_state.py",         # #1 arm_lineage (conformance-gated arm)
    "scripts/lineage_daemon/wal/green_wake.py",       # #3/#4 confirm-started + wake-green
    # identity_reconciler v1 (DEC-1789507884583046): periodic pass composing the
    # CID_RESOLVER_REGISTRY/SERVICE_PROBES — must stay runtime-name-literal-free too.
    "scripts/identity_store/identity_reconciler.py",
]
RUNTIME_LITERALS = ("claude", "codex", "gemini", "antigravity")


def test_no_runtime_name_literal_in_core():
    """the operator's structural rule: no runtime=='x' / runtime-name literal in the rotation
    core. Grep the core files; a hit is a defect. (Comments are stripped-ish: we flag any
    occurrence in a line that is not a pure comment, to catch conditionals + sets.)"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    offenders = []
    for rel in CORE_FILES:
        path = os.path.join(root, rel)
        with open(path) as fh:
            for i, line in enumerate(fh, 1):
                code = line.split("#", 1)[0]  # drop trailing comment
                low = code.lower()
                for lit in RUNTIME_LITERALS:
                    if lit in low:
                        offenders.append(f"{rel}:{i}: {code.strip()}")
    assert not offenders, "runtime-name literal in core:\n" + "\n".join(offenders)
