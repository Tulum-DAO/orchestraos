"""self-retire autonomy layer (self-retire-builder) — the REAL
`is_graduated_autoretire`.

Fills completion-fix Stage C's `False` stub in `graduation_retire.py`. Returns
`True` (=> `REQUIRE_CARD` False => the promote-graded card is SKIPPED) ONLY for a
cold-verified graduated-T2 seat. Everything else (any missing/uncertain signal)
=> `False` => the human card stays (fail-toward-not-acting, same KEEP-polarity as
`may_retire`).

Four independent AND-gates (SPEC §5/§8/§11; D4/D6/D8). Positive-signal-only:
  1. kill-off  `~/runtime/SELF_RETIRE_DISABLED` ABSENT. Present -> False for every
               seat instantly (invariant #4). Defense-in-depth: the substrate's
               `require_card` ALSO forces the card when present; checking here too
               means the predicate itself is honest in isolation.
  2. tier      the seat's lineage tier ∈ `ARMED_TIERS` (this wave = {"T2"}),
               resolved from the DURABLE registry — never self-reported by the seat.
  3. armed     the lineage is opted-in via the single-source arming allowlist
               `~/runtime/self_retire_armed` (one lineage root per line; `#`
               comments + blanks ignored). One greppable switch, widened deliberately.
  4. graded    the successor's deterministic comprehension-gate verdict is an
               affirmative STRICT PASS: `state/agent-handoffs/<successor>.comprehension.json`
               `pass == true` AND `mode == "strict"` (D4 + DEC-1787728346 — NOT
               multi-model congruence). A supervised grade, an absent `mode` key
               (pre-rebuild artifacts), missing/unreadable/false => not a pass.
               Unattended auto-retire may consume ONLY strict grades.

This module does NOT touch `may_retire`, KILL GATE 1, the retire writer, or
`require_card`'s definition. own-work-vs-churn safety lives inside `may_retire`
(upstream); the atomic single-writer retire is downstream — both untouched here.

All source paths are keyword-only injectables with production defaults, so the
frozen `graduated_fn(seat)` call shape stays exactly `is_graduated_autoretire(seat)`
while tests drive an isolated world.
"""
import json
import os

# This wave arms T2 ONLY. Widen to add T1 (after T2 proven); GM LAST (D1/§8).
ARMED_TIERS = frozenset({"T2"})

_HOME = os.path.expanduser("~")
# Non-synced runtime e-brake + arming knob (fleet convention: ~/runtime is OUTSIDE
# Syncthing so a stale Mac copy can't resurrect/re-arm them).
_RUNTIME_DIR = os.environ.get("ORCH_RUNTIME_DIR", os.path.join(_HOME, "runtime"))
_DEFAULT_DISABLED_PATH = os.path.join(_RUNTIME_DIR, "SELF_RETIRE_DISABLED")
_DEFAULT_ARMED_PATH = os.path.join(_RUNTIME_DIR, "self_retire_armed")

# repo root = scripts/lineage_daemon/self_retire_gate.py -> three dirs up. In a
# worktree this resolves to that worktree's own registry / handoffs.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_REGISTRY_PATH = os.path.join(_REPO_ROOT, "registry.json")
_DEFAULT_HANDOFFS_DIR = os.path.join(_REPO_ROOT, "state", "agent-handoffs")


def _lineage_of(seat):
    """The registry key for the seat's lineage (prefer the durable lineage_root,
    fall back to the gen-specific agent_id). None if the seat carries neither."""
    if not isinstance(seat, dict):
        return None
    return seat.get("lineage_root") or seat.get("agent_id") or None


def _kill_switch_on(disabled_path):
    """True if the e-brake is present OR we can't even check it (fail-safe: any
    uncertainty about the brake -> treat as ON -> not-skip)."""
    try:
        return os.path.exists(disabled_path)
    except OSError:
        return True


def _seat_tier(seat, registry_path):
    """The seat's tier from the DURABLE registry (`agents[lineage].tier`), never
    the seat's self-report. Unknown seat / unreadable registry -> None (no
    affirmative tier signal)."""
    lineage = _lineage_of(seat)
    if not lineage:
        return None
    try:
        with open(registry_path) as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return None
    agents = reg.get("agents") if isinstance(reg, dict) else None
    row = agents.get(lineage) if isinstance(agents, dict) else None
    return row.get("tier") if isinstance(row, dict) else None


def _lineage_armed(seat, armed_path):
    """The arming knob — a single greppable allowlist (one lineage root per line;
    `#` comments + blanks ignored). Absent file / seat not listed -> not armed."""
    lineage = _lineage_of(seat)
    if not lineage:
        return False
    try:
        with open(armed_path) as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    armed = {s for s in (ln.strip() for ln in lines) if s and not s.startswith("#")}
    return lineage in armed


def _graduation_pass(seat, handoffs_dir):
    """D4 grade source: the successor's deterministic comprehension-gate verdict.
    `<successor>.comprehension.json` `pass == true` AND `mode == "strict"`.

    STRICT-MODE ARMING PRECONDITION (DEC-1787728346): the rebuilt KEY-1 grader
    (rotation_gate_manual.record_readback) writes a top-level `mode` of
    "strict"|"supervised". An UNATTENDED auto-retire consumer (this predicate) may
    consume ONLY a strict grade — supervised tolerates <=1 weakly-cited answer and
    is human-review-only. So pass==true alone is NOT a graduation. Missing/unreadable
    file, pass!=true, a supervised grade, or an absent `mode` key (every pre-rebuild
    artifact) -> NOT a pass (no-data-is-not-permission; fail toward the card)."""
    if not isinstance(seat, dict):
        return False
    successor = seat.get("successor")
    if not successor:
        return False
    path = os.path.join(handoffs_dir, f"{successor}.comprehension.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict)
            and data.get("pass") is True
            and data.get("mode") == "strict")


def is_graduated_autoretire(seat, *,
                            disabled_path=_DEFAULT_DISABLED_PATH,
                            armed_path=_DEFAULT_ARMED_PATH,
                            registry_path=_DEFAULT_REGISTRY_PATH,
                            handoffs_dir=_DEFAULT_HANDOFFS_DIR,
                            armed_tiers=ARMED_TIERS) -> bool:
    """True ONLY for a cold-verified graduated-T2 seat (all four gates PASS);
    False on ANY missing/uncertain signal. Never widen without all four."""
    if _kill_switch_on(disabled_path):
        return False
    if _seat_tier(seat, registry_path) not in armed_tiers:
        return False
    if not _lineage_armed(seat, armed_path):
        return False
    if not _graduation_pass(seat, handoffs_dir):
        return False
    return True


# --- D2: notify-only ping on each card-skipped retire --------------------------

def build_skip_notice(seat, *, reason=None, used_pct=None) -> str:
    """The per-retire visibility line that REPLACES the human tap on a card-skipped
    (graduated-T2) retire — names the seat, its ctx%, and the `may_retire` reason.
    Pure; no send."""
    pred = seat.get("agent_id") if isinstance(seat, dict) else seat
    succ = seat.get("successor") if isinstance(seat, dict) else None
    ctx = f"{used_pct}%" if used_pct is not None else "n/a"
    return (f"[self-retire] card SKIPPED (graduated-T2): retiring {pred} "
            f"(successor {succ}, ctx {ctx}) — {reason or 'may_retire=RETIRE'}")


def notify_card_skipped(seat, *, reason=None, used_pct=None, send_fn) -> str:
    """Emit the notify-only ping (D2). Notify-only: a failed send is swallowed so
    it can NEVER abort the retire path. Returns the message string."""
    msg = build_skip_notice(seat, reason=reason, used_pct=used_pct)
    try:
        send_fn(msg)
    except Exception:  # noqa: BLE001 -- notify-only: a ping failure never blocks retire
        pass
    return msg
