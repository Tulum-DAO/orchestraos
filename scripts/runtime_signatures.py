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
import re
from pathlib import Path

import sys
# The ONE data-dir default is orchestra_cli.settings.data_dir (data-dir sweep S5); orchestra_cli
# lives in this file's checkout, appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402


# Registry is the single source of an agent's declared runtime (never inferred
# from a pane/transcript). ORCHESTRA_DIR-relative so a worktree/scratch run can
# repoint it via env, matching message-router.py's own resolution.
ORCHESTRA_DIR = Path(
    os.environ.get("ORCHESTRA_DIR", str(_data_dir()))
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
    # Grok Build CLI (`grok`). Its composer is NOT a verified delivery surface: in the
    # --minimal mode seats run in it draws a bare '❯' (the same glyph as Claude) with a dim
    # placeholder, and pane text alone cannot tell the two apart. So prompt_char is a
    # NUL-led sentinel that can never occur in a tmux capture: every pane-text consumer
    # (wait_for_tui, boot_inject, the router's composer/typed checks, stuck-injection
    # cleanup) finds NO prompt and takes its fail-safe branch (not-ready / not-idle /
    # nothing to clean). Busy/idle comes from the session's own events.jsonl
    # (scripts/grok_context.py). With "verified": False the router HOLDS grok mail (it stays
    # pending, never dropped) and the gateway refuses to type into the pane; the seat is woken
    # for mail by scripts/grok_mail_watch.py, which reads the composer's colours itself.
    "grok": {
        "prompt_char": "\x00grok-composer-unverified",
        "has_ghost_suggestions": False,
        "idle_footer_re": None,
        "verified": False,
        "idle_source": "events.jsonl",
    },
}
DEFAULT_RUNTIME = "claude"

#: Runtimes whose "Phase 2" surfaces (WAL capture, rotation, approval cards / menus) are NOT
#: implemented. Every one of those entry points refuses these runtimes EXPLICITLY (named
#: reason) instead of falling through to a claude default. (Automatic resume after a reboot
#: IS supported for grok: scripts/recovery_runtime.py.)
PHASE2_UNSUPPORTED_RUNTIMES = frozenset({"grok"})


class Phase2Unsupported(RuntimeError):
    """A Phase-2 surface (WAL capture, rotation, cards, recovery) was asked to act on a
    runtime that only has see/spawn/message/resume support."""


def refuse_if_phase2_unsupported(runtime, surface: str) -> None:
    """Raise Phase2Unsupported when ``runtime`` has no ``surface`` adapter yet."""
    rt = (runtime or "").strip().lower()
    if rt in PHASE2_UNSUPPORTED_RUNTIMES:
        raise Phase2Unsupported(
            f"runtime={rt!r} has no {surface} adapter (not implemented for this "
            f"runtime) — refusing rather than defaulting to claude")


def signature_verified(runtime) -> bool:
    """False only for a signature explicitly marked unverified (grok)."""
    sig = PROMPT_SIGNATURES.get((runtime or "").strip().lower()) or {}
    return bool(sig.get("verified", True))


# CSI, including SGR. A pane captured with tmux -e leads the prompt with ESC[0m and may wrap
# footer characters in SGR; matching the raw bytes would read the composer as absent.
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_csi(text: str) -> str:
    return _CSI_RE.sub("", text or "")


def grok_composer_line_state(capture: str, sig: dict | None = None) -> str:
    """Classify the live Grok composer. Returns 'empty', 'typed', or 'absent'.

    The live composer is the last line that is only the prompt char, or the last line that
    starts with the prompt char and has more text, whose next non-empty line matches the
    signature's idle_footer_re. CSI/SGR is stripped before either comparison, because the
    router captures with tmux -e. A prompt glyph that appears only in scrollback, with no
    footer under it, is 'absent' and must not read as a composer. A signature with no footer
    pattern, or with a sentinel prompt (the unverified default), is always 'absent'. This does
    not decide busy vs idle: the footer stays on screen during a turn."""
    sig = sig if sig is not None else PROMPT_SIGNATURES["grok"]
    prompt = sig.get("prompt_char") or ""
    footer_re = sig.get("idle_footer_re") or ""
    if not prompt or not footer_re or "\x00" in prompt:
        return "absent"
    lines = (capture or "").splitlines()
    found = None
    for i, line in enumerate(lines):
        stripped = _strip_csi(line).strip()
        if not stripped.startswith(prompt):
            continue
        nxt = ""
        for later in lines[i + 1:]:
            later_s = _strip_csi(later).strip()
            if later_s:
                nxt = later_s
                break
        if not re.search(footer_re, nxt):
            continue
        found = "empty" if stripped == prompt else "typed"
    return found or "absent"


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


def declared_runtime_disabled() -> bool:
    """KILL SWITCH for declared-runtime routing (DEC-1791656515249463): instant, no deploy.
      env  ROUTER_DECLARED_RUNTIME_DISABLED=1   OR   sentinel ~/runtime/ROUTER_DECLARED_RUNTIME_DISABLED
    When set the router ignores declarations and behaves exactly as before them (claude, or the
    inferred runtime when ROUTER_GEMINI_IDLE_ARMED is armed). Different from the arm: the arm ALSO
    infers undeclared rows; the kill switch only turns declarations off."""
    if os.environ.get("ROUTER_DECLARED_RUNTIME_DISABLED") == "1":
        return True
    try:
        return (Path.home() / "runtime" / "ROUTER_DECLARED_RUNTIME_DISABLED").exists()
    except OSError:
        return False


def declared_runtime(agent_id: str, meta: dict | None = None,
                     registry_path: "Path | str | None" = None) -> "tuple[str | None, str | None]":
    """(runtime, unknown) from the registry row's EXPLICIT `runtime` field only — no inference
    from the model id (DEC-1791656515249463 Q1).
      runtime: the field when it names a known signature ('agy' is the gemini runtime), else None.
      unknown: the raw value when the field is set but names no signature (a typo, or a runtime
               whose signature has not shipped), else None. The caller warns once and falls back.
    Both None when the row or the field is absent."""
    reg_path = REGISTRY if registry_path is None else registry_path
    try:
        reg = json.loads(Path(reg_path).read_text())
        entry = reg.get("agents", {}).get(agent_id) or {}
    except (json.JSONDecodeError, OSError, AttributeError):
        return None, None
    raw = entry.get("runtime") if isinstance(entry, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    rt = raw.strip().lower()
    rt = "gemini" if rt == "agy" else rt
    if rt in PROMPT_SIGNATURES:
        return rt, None
    return None, raw.strip()


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
    if model.startswith("grok"):
        # An undeclared grok-* row must resolve to grok (whose gate HOLDS), never fail
        # open to claude's ❯ signature.
        return "grok"
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
# 'grok': a Grok Build seat. Like codex it is positive-declaration only: resolve_runtime does
# not derive it from a grok-* model, so a seat verb needs runtime='grok' on the row.
VALID_RUNTIMES = ("claude", "gemini", "codex", "service", "grok")


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


def model_runtime(model) -> "str | None":
    """Runtime a model id POSITIVELY names, or None (no signal). Prefix rules only:
    claude-* -> claude; gemini-* -> gemini; gpt-* / o<digit>* / codex-* -> codex; grok-* -> grok.
    Never guesses: an unknown family is None, not claude."""
    m = (model or "").strip().lower()
    if not m:
        return None
    if m.startswith("claude"):
        return "claude"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith(("gpt-", "codex")) or (len(m) >= 2 and m[0] == "o" and m[1].isdigit()):
        return "codex"
    if m.startswith("grok"):
        return "grok"
    return None


def validate_model_for_runtime(runtime, model, *, agent_id=None):
    """Issue #92: refuse a model id that positively names ANOTHER runtime (a Codex seat
    launched with --model claude-sonnet-5 exits and its init prompt lands in bare bash).
    A model with no runtime signal is not a mismatch. Raises RuntimeResolutionError."""
    rt = _norm_runtime_token(runtime)
    mr = model_runtime(model)
    if mr is not None and rt in VALID_RUNTIMES and mr != rt:
        who = f" for agent {agent_id!r}" if agent_id else ""
        raise RuntimeResolutionError(
            f"model {model!r} belongs to runtime '{mr}' but the seat{who} is declared "
            f"runtime '{rt}' — refusing to spawn (the CLI would exit and the init prompt "
            f"would be typed into a bare shell); fix AGENT_MODEL or the registry runtime")


def validate_runtime_for_registration(record, *, agent_id=None):
    """Registration invariant (spec §4.1 R4(a)): a NEW registry row MUST carry a
    resolvable runtime so the zero-signal class cannot regrow. Returns the
    resolved runtime or raises RuntimeResolutionError. 'service' and 'codex' are
    VALID declared rows (a service is a legit non-seat process row; codex is a
    Phase-2 seat) — this gate validates DECLARATION, not spawnability."""
    return resolve_runtime(record, agent_id=agent_id, strict=True)
