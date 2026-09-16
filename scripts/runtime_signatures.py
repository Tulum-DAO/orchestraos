"""Per-runtime prompt signatures + runtime resolution — the ONE source.

Extracted from message-router.py (R1, all-model-parity spec §4.2) so BOTH the
router idle-gate AND spawn's ready-wait consume the SAME table instead of each
hardcoding a prompt char. Precedent: lane_charter.py (extracted so send + receipt
share one rule). This module is byte-equivalent to the router's prior inline
definitions — no behavior change; an unknown runtime still fails safe to 'claude'
(never widen the idle surface for an unrecognized agent — the fleet
'success that isn't' defect class).

Consumers: message-router.py (idle-gate), and later spawn-agent dispatch (§4.2).
"""
import json
import os
from pathlib import Path

# Registry is the single source of an agent's declared runtime (never inferred
# from a pane/transcript). ORCHESTRA_DIR-relative so a worktree/scratch run can
# repoint it via env, matching message-router.py's own resolution.
ORCHESTRA_DIR = Path(
    os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
)
REGISTRY = ORCHESTRA_DIR / "registry.json"

# --- Per-runtime prompt signatures (the operator-approved apr_a72c10f8, 2026-08-23) ---
# The idle-gate used a single hardcoded ❯, so agy/Gemini panes (which render '>')
# were NEVER seen as idle and the ENTIRE Gemini fleet's inbound mail was held
# (19 dead-letters + 26 pending on 08-23, incl 2 of the operator's own). The fix keys the
# prompt signature off the AGENT'S RUNTIME (not a 2nd hardcoded global char), so
# each runtime's real TUI is recognized. Grounded by effect on live gemini panes.
PROMPT_SIGNATURES = {
    # Claude Code TUI: ❯ prompt; ghost suggestions are SGR-dim; cursor is SGR-reverse.
    "claude": {
        "prompt_char": "\u276f",
        "has_ghost_suggestions": True,   # dim-SGR ghost text => the SGR walk in input_line_state
        "idle_footer_re": None,          # ❯-present is sufficient (existing behavior, unchanged)
    },
    # agy / Gemini CLI: '>' prompt (SGR 38;5;111 when idle). Idle footer is the
    # '? for shortcuts … Gemini <ver> Flash|Pro · high' status line — present on an
    # idle pane, absent mid-turn (verified by effect across 10 live gemini panes).
    # No SGR-dim ghost-suggestion class => any non-space visible char after '>' is
    # human-typed.
    "gemini": {
        "prompt_char": ">",
        "has_ghost_suggestions": False,
        "idle_footer_re": r"\? for shortcuts|Gemini [\d.]+ (Flash|Pro)",
    },
    # Codex CLI (0.148+): '›' (U+203A) composer prompt, bold; the idle composer
    # carries an SGR-dim 'Ask Codex to do anything' placeholder. History echoes
    # ALSO start with '›' (dim), so the prompt char alone is ambiguous — idle
    # requires the positive placeholder footer. Working marker: '◦ Working
    # (Ns • esc to interrupt)'. Verified by effect on codex-dev-1 2026-08-27
    # (dossier .workspace/codex-parity-dossier/, fixtures + ledger rows 1/2/21).
    "codex": {
        "prompt_char": "\u203a",
        # The idle composer renders an SGR-dim 'Ask Codex to do anything'
        # placeholder — dim ghost text exactly like claude's suggestions; the
        # SGR walk must classify it ghost, NOT typed (else the router reads a
        # human mid-composition forever and never delivers).
        "has_ghost_suggestions": True,
        "idle_footer_re": r"Ask Codex to do anything",
    },
}
DEFAULT_RUNTIME = "claude"


def is_claude_stop_hook_seat(runtime: "str | None") -> bool:
    """The ONE narrow CORE seat predicate, single-sourced across DELIVERY and
    ROTATION (DEC-1787657323). True iff the seat runs in the Claude harness —
    the only runtime where a Claude Stop hook fires (delivery drain, rotation
    self-trigger). Pure, no IO.

    Byte-behavior-identical to turn-boundary-hook-dev's committed inline check
    `(runtime or DEFAULT_RUNTIME) == 'claude'` so its A2 `_pull_covered` swap is
    a behavior-neutral one-liner. A falsy runtime resolves to DEFAULT_RUNTIME
    ('claude') — the historical fail-safe (an unrecognized seat is treated as
    Claude-covered; each domain layers its OWN stricter gate on top):
      DELIVERY:  _pull_covered      = CORE AND (canon=='gm' OR allowlist-covered)
      ROTATION:  rotation_eligible  = CORE AND (not retired/quiescent AND adapter)
    Neither domain inherits the other's gate; only runtime=='claude' is CORE.
    Non-claude runtimes (gemini/agy/codex/service) have no Stop hook / rotation
    adapter — this is the predicate that ends codex-dev-1's 136x false-fire class.
    """
    return (runtime or DEFAULT_RUNTIME) == "claude"


# The Claude prompt char, single-sourced from the table so it can never drift
# from PROMPT_SIGNATURES["claude"] (❯; kept for the idle-detection back-compat
# paths that scan for the Claude prompt specifically).
PROMPT_CHAR = PROMPT_SIGNATURES["claude"]["prompt_char"]


def gemini_idle_routing_armed() -> bool:
    """ARMING GATE (the operator apr_a72c10f8 — build-in-shadow, do NOT arm without the operator).

    The router runs from SOURCE via the per-minute cron, so a source edit is live
    on the next tick — there is no separate compiled 'apply'. To keep this change
    genuinely gated (gm's apply-to-live gate) the per-runtime Gemini idle-gate is
    DORMANT until the operator arms it, instant + reversible with no deploy:
      env  ROUTER_GEMINI_IDLE_ARMED=1   OR   sentinel ~/runtime/ROUTER_GEMINI_IDLE_ARMED
    When UNARMED every agent resolves to 'claude' (the historical signature), so
    behavior is byte-identical to before this change — safe to sit live-in-shadow."""
    if os.environ.get("ROUTER_GEMINI_IDLE_ARMED") == "1":
        return True
    try:
        return (Path.home() / "runtime" / "ROUTER_GEMINI_IDLE_ARMED").exists()
    except OSError:
        return False


def agent_runtime(agent_id: str, meta: dict | None = None,
                  registry_path: "Path | str | None" = None) -> str:
    """Resolve an agent's runtime for prompt-signature selection.

    Source of truth is registry.json (`runtime` field for agy/Gemini agents;
    Claude agents carry `model: claude-*`). agent-sessions.json (what
    load_agent_meta reads) does NOT carry runtime, so we read the registry.
    Fail-safe: unknown => 'claude' (the historical default; never widens the
    idle surface for an unrecognized agent).

    registry_path overrides the default REGISTRY location (injection point for
    tests / a scratch registry); production callers pass nothing and read the
    live registry exactly as before."""
    reg_path = REGISTRY if registry_path is None else registry_path
    try:
        reg = json.loads(Path(reg_path).read_text())
        entry = reg.get("agents", {}).get(agent_id) or {}
    except (json.JSONDecodeError, OSError):
        entry = {}
    rt = (entry.get("runtime") or "").strip().lower()
    if rt in PROMPT_SIGNATURES:
        return rt
    model = (entry.get("model") or "").strip().lower()
    if model.startswith("claude"):
        return "claude"
    if rt in ("agy", "gemini") or model.startswith("gemini"):
        return "gemini"
    return DEFAULT_RUNTIME


# --- Strict seat-verb runtime resolution (spec §4.1 R4(b), all-model-parity) ---
# `agent_runtime` above is the ROUTER's resolver: it FAILS OPEN to 'claude' (an
# unrecognized pane must never widen the idle surface — delivery, not identity).
# The SEAT verbs (spawn + promote) have the OPPOSITE failure policy: a wrong
# runtime default is silent seat corruption (a Gemini seat resumed as
# `claude --resume` — the "success that isn't" fleet defect), so they REFUSE a
# zero-signal/unknown runtime. R3 left spawn reading only `runtime` while promote
# read `runtime|provider` (a fail-safe asymmetry); R4 UNIFIES both on this ONE
# resolver, which reads `runtime` ONLY (never `provider`) — every live row's
# `provider` is redundant with `runtime` (codex-dev-1 carries both), so the alias
# adds no signal and dropping it removes a second field that could disagree.
VALID_RUNTIMES = ("claude", "gemini", "codex", "service")


class RuntimeResolutionError(ValueError):
    """A registry entry carries no resolvable runtime (zero-signal) or an
    explicit-but-unknown one — the seat verbs and the registration invariant
    REFUSE rather than defaulting to claude."""


def _norm_runtime_token(raw) -> str:
    rt = (raw or "").strip().lower()
    return "gemini" if rt == "agy" else rt


def resolve_runtime(entry, *, agent_id=None, strict=True):
    """The ONE shared runtime resolution for the seat verbs (spawn + promote).

    Positive-signal only; reads `runtime`, NEVER `provider` (§4.1 R4(b)):
      1. explicit `runtime` (agy → gemini) if a known runtime → return it,
         including the terminal 'service' and the Phase-2 'codex' values.
      2. else derive from a POSITIVE model signal (claude-* / gemini-*) — the
         same spec-legal derivation the R1b migrator uses.
      3. else ZERO-SIGNAL. strict=True → raise RuntimeResolutionError (naming the
         --declare path). strict=False → return None, for promote's legacy
         transcript-UNION fallback (a SEARCH heuristic for un-backfilled rows,
         never a resume/spawn default).
    An explicit-but-unknown runtime with no model signal is refused in strict
    mode — never guessed."""
    entry = entry or {}
    rt = _norm_runtime_token(entry.get("runtime"))
    if rt in VALID_RUNTIMES:
        return rt
    model = (entry.get("model") or "").strip().lower()
    if model.startswith("claude"):
        return "claude"
    if model.startswith("gemini"):
        return "gemini"
    if not strict:
        return None
    who = f" for agent {agent_id!r}" if agent_id else ""
    if rt:
        raise RuntimeResolutionError(
            f"unknown runtime {rt!r}{who} with no model signal — declare a valid "
            f"runtime ({'|'.join(VALID_RUNTIMES)}) via "
            f"backfill_registry_runtime.py --declare before use (spec §4.1); "
            f"never default to claude")
    raise RuntimeResolutionError(
        f"zero-signal registry entry{who} (runtime AND model both unset) — a seat "
        f"verb refuses rather than defaulting to claude; declare via "
        f"backfill_registry_runtime.py --declare (spec §4.1 R4(a))")


def validate_runtime_for_registration(record, *, agent_id=None):
    """Registration invariant (spec §4.1 R4(a)): a NEW registry row MUST carry a
    resolvable runtime so the zero-signal class cannot regrow. Returns the
    resolved runtime or raises RuntimeResolutionError. 'service' and 'codex' are
    VALID declared rows (a service is a legit non-seat process row; codex is a
    Phase-2 seat) — this gate validates DECLARATION, not spawnability."""
    return resolve_runtime(record, agent_id=agent_id, strict=True)
