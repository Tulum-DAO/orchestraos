"""Fixed handoff schema (schema, not prose) -- the mid-phase resume anchor.

Ambient state (cwd / model / lineage) is intentionally NOT part of this
schema: the successor rehydrates it from the registry. This module only
captures the goal-and-phase context needed to resume mid-phase.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List

# --- R1 responder-side: outbound completion-callbacks carried across a handoff -
#
# When agent B is doing work FOR agent A and owes A a reply on completion, that
# pending reply is an "outbound callback." If B rotates mid-work, its successor
# B' must inherit and fire the callback exactly once. We model each such callback
# as a structured entry inside `open_loops` (a plain string loop stays a plain
# string — backward compatible). agy-ops refinement (DEC-1786573544): every
# callback carries a state `pending`->`sent`; a fired inherited callback is
# marked `sent` so it is NEVER double-emitted (idempotent handoff callbacks).

CALLBACK_KIND = "callback"
CB_PENDING = "pending"
CB_SENT = "sent"

# --- v2 richness thresholds (DEC-1786724046) ---
# Inlined here (NOT imported from focus_registry) to keep the daemon schema
# import-light and avoid a circular import via lineage_init. PB's
# scripts/focus_registry/richness.py stays the tested reference; these mirror it.
RICHNESS_MIN_CANARY = 3
RICHNESS_STALENESS_MAX_AGE_S = 3600.0


def min_decisions_for(session_turns: int) -> int:
    """min decisions-with-rationale scaled by session length:
    clamp(1, ceil(turns/60), 3). 1 if <60 turns, 2 if 60-150, 3 if >150."""
    import math
    turns = session_turns or 0
    return max(1, min(math.ceil(turns / 60) if turns > 0 else 1, 3))


def is_stale(handoff_mtime: float, now: float,
             max_age_s: float = RICHNESS_STALENESS_MAX_AGE_S) -> bool:
    """True if the handoff is older than max_age_s (mtime vs now) — a handoff
    authored at soft and consumed at hard hours later is stale → re-author."""
    return (now - handoff_mtime) > max_age_s


def make_callback(to, what_for, request_id, state=CB_PENDING) -> dict:
    """Build a structured outbound-callback open_loop entry.

    to:         the agent this callback owes a reply (the originator A).
    what_for:   short description of what the reply is for.
    request_id: the originating request id (the thing being answered) — the
                idempotency key a successor uses to avoid double-emitting.
    state:      'pending' (not yet sent) or 'sent' (already fired — inert).
    """
    return {
        "kind": CALLBACK_KIND,
        "to": to,
        "what_for": what_for,
        "request_id": request_id,
        "state": state,
    }


def is_callback(loop) -> bool:
    """True iff an open_loops entry is a structured outbound callback."""
    return isinstance(loop, dict) and loop.get("kind") == CALLBACK_KIND


def mark_callback_sent(open_loops, request_id) -> list:
    """Return a NEW open_loops list with the callback(s) matching request_id
    marked state='sent'. Idempotent: a callback already 'sent' is unchanged, and
    a request_id with no match returns the list untouched (copied). Plain-string
    loops pass through verbatim."""
    out = []
    for loop in open_loops:
        if is_callback(loop) and loop.get("request_id") == request_id:
            nxt = dict(loop)
            nxt["state"] = CB_SENT
            out.append(nxt)
        else:
            out.append(loop)
    return out


@dataclass
class PhaseState:
    plan_ref: str = ""
    phase_n: int = 0
    phase_m: int = 0
    gates_passed: list = field(default_factory=list)
    gates_total: int = 0
    current_step: str = ""
    next_gate: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PhaseState":
        d = d or {}
        return cls(
            plan_ref=d.get("plan_ref", ""),
            phase_n=d.get("phase_n", 0),
            phase_m=d.get("phase_m", 0),
            gates_passed=list(d.get("gates_passed", [])),
            gates_total=d.get("gates_total", 0),
            current_step=d.get("current_step", ""),
            next_gate=d.get("next_gate", ""),
        )


@dataclass
class Handoff:
    current_goal: str = ""
    phase_state: PhaseState = field(default_factory=PhaseState)
    working_state: str = ""
    open_loops: list = field(default_factory=list)
    # each decision is a dict {"text": str, "rationale": str}
    decisions: list = field(default_factory=list)
    file_roots_touched: list = field(default_factory=list)
    next_3_actions: list = field(default_factory=list)
    # --- v2 red-team-hardened fields (DEC-1786724046) ---
    # canary_questions: each {"id": str, "question": str, "source_pointer": str}.
    # THERE IS NO ANSWER KEY — anywhere, ever (the operator ruling 2026-08-18). The old
    # {id,question,answer} form put the answers in the committed handoff the
    # successor must read IN FULL, so every daemon-driven rotation produced a
    # compromised gate BY DESIGN. Vaulting the answers elsewhere was rejected too:
    # that PROTECTS a secret (a lock can be picked, mis-pathed, or copied by the
    # next refactor) instead of DELETING it. source_pointer names where the fact
    # lives in the predecessor's jsonl; the grader verifies the successor's own
    # words against that immutable transcript at grade time. Leaking the artifact
    # is then harmless by construction — it only says "go read the transcript",
    # which is exactly what protocol-v2 wants.
    canary_questions: list = field(default_factory=list)
    # hazards: list of str (top-3 live hazards) — required in the read-back.
    hazards: list = field(default_factory=list)
    # first_effect: {"kind": file|commit|session|msg|command, "target": str,
    # "check": str|None} — the declared first checkable effect (H6 task anchor).
    first_effect: dict = field(default_factory=dict)

    # --- serialization ---

    def to_dict(self) -> dict:
        return {
            "current_goal": self.current_goal,
            "phase_state": self.phase_state.to_dict(),
            "working_state": self.working_state,
            "open_loops": list(self.open_loops),
            "decisions": list(self.decisions),
            "file_roots_touched": list(self.file_roots_touched),
            "next_3_actions": list(self.next_3_actions),
            "canary_questions": list(self.canary_questions),
            "hazards": list(self.hazards),
            "first_effect": dict(self.first_effect),
        }

    def to_successor_init(self) -> dict:
        """REDACTED form for any successor-readable surface (init task + the
        committed HANDOFF doc). Strips every canary_questions[].answer — the
        successor sees the QUESTIONS only; the daemon holds the answers in
        process (Finding 0 anti-Goodhart). Everything else is identical to
        to_dict()."""
        d = self.to_dict()
        # Belt only: with no answer key authored, the redacted and full forms
        # carry the same facts. The POINTER is deliberately kept — telling the
        # successor where to read is the point of protocol-v2, not a leak.
        d["canary_questions"] = [
            {"id": q.get("id"), "question": q.get("question"),
             "source_pointer": q.get("source_pointer")}
            for q in self.canary_questions
        ]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "Handoff":
        d = d or {}
        return cls(
            current_goal=d.get("current_goal", ""),
            phase_state=PhaseState.from_dict(d.get("phase_state", {})),
            working_state=d.get("working_state", ""),
            open_loops=list(d.get("open_loops", [])),
            decisions=list(d.get("decisions", [])),
            file_roots_touched=list(d.get("file_roots_touched", [])),
            next_3_actions=list(d.get("next_3_actions", [])),
            canary_questions=list(d.get("canary_questions", [])),
            hazards=list(d.get("hazards", [])),
            first_effect=dict(d.get("first_effect", {}) or {}),
        )

    @classmethod
    def from_json(cls, s: str) -> "Handoff":
        return cls.from_dict(json.loads(s))

    # --- R1 responder-side: inherited outbound callbacks ---

    def pending_callbacks(self) -> List[dict]:
        """The outbound callbacks still owed (state='pending'). A successor
        inherits exactly these and must fire each once."""
        return [l for l in self.open_loops
                if is_callback(l) and l.get("state") == CB_PENDING]

    def fire_inherited_callbacks(self, send, resolve=None) -> List[str]:
        """Fire each PENDING inherited callback exactly once, then mark it sent.

        `send(to, callback)` is the injected delivery seam (msg_store send /
        gateway inject) — kept injectable so this stays pure + hermetic in tests.
        `resolve(to) -> target` optionally maps the callback's `to` agent to a
        live-head session before send (the R2 primitive); when None, `send`
        receives the raw `to`.

        Idempotency: only 'pending' callbacks are fired, and each is flipped to
        'sent' in self.open_loops immediately after a successful send. Re-running
        (e.g. a second successor, or a retry) fires nothing already sent — the
        callback is NEVER double-emitted. Returns the request_ids fired."""
        fired: List[str] = []
        for cb in self.pending_callbacks():
            target = resolve(cb["to"]) if resolve else cb["to"]
            send(target, cb)
            self.open_loops = mark_callback_sent(self.open_loops, cb["request_id"])
            fired.append(cb["request_id"])
        return fired

    # --- validation ---

    def validate(self, session_turns: int = 0, require_richness: bool = False) -> List[str]:
        """Return a list of error strings; empty list == valid.

        Structural validation always runs. When `require_richness=True` (the
        v2 hard_rotate gate, DEC-1786724046) the min-richness thresholds also
        apply: next_gate non-empty, >=min_decisions_for(session_turns) decisions
        WITH rationale, non-empty open_loops, >=RICHNESS_MIN_CANARY canary
        questions, non-empty hazards, first_effect present. This makes a
        hollow-but-parseable handoff FAIL before spawn (H2)."""
        errors: List[str] = []

        if not self.current_goal:
            errors.append("current_goal must be non-empty")

        ps = self.phase_state
        if ps.phase_m > 0 and not (1 <= ps.phase_n <= ps.phase_m):
            errors.append(
                "phase_n must be in 1..phase_m when phase_m > 0 "
                f"(got phase_n={ps.phase_n}, phase_m={ps.phase_m})"
            )

        for i, dec in enumerate(self.decisions):
            if not dec.get("text"):
                errors.append(f"decision[{i}] must have non-empty text")

        for i, loop in enumerate(self.open_loops):
            if is_callback(loop):
                if not loop.get("to"):
                    errors.append(f"open_loops[{i}] callback must have a 'to'")
                if not loop.get("request_id"):
                    errors.append(
                        f"open_loops[{i}] callback must have a 'request_id'")
                if loop.get("state") not in (CB_PENDING, CB_SENT):
                    errors.append(
                        f"open_loops[{i}] callback state must be "
                        f"'{CB_PENDING}' or '{CB_SENT}'")

        if not self.next_3_actions:
            errors.append("next_3_actions must be non-empty")

        # LEAK LINT — structural, runs ALWAYS (not just under richness): the
        # committed doc is the artifact that leaked, and it is hand-authored, so
        # an output-side redaction never saw it (initiative-architect's point:
        # lint the COMMITTED DOC, not the generated output). Defense in depth
        # only — with no answer key authored anywhere there is nothing to catch.
        for q in self.canary_questions:
            if not isinstance(q, dict):
                continue
            leaked = [k for k in q
                      if str(k).strip().lower() in ("answer", "expected",
                                                    "expected_answer", "answers")]
            if leaked:
                errors.append(
                    f"canary {q.get('id') or '?'} carries an ANSWER KEY "
                    f"({', '.join(sorted(leaked))}) — there is no answer key, "
                    f"ever; store source_pointer and let the grader verify "
                    f"against the predecessor jsonl")

        if require_richness:
            if not ps.next_gate:
                errors.append("richness: next_gate empty (no resume anchor)")
            good_decisions = [
                d for d in self.decisions
                if d.get("text") and d.get("rationale")
            ]
            need = min_decisions_for(session_turns)
            if len(good_decisions) < need:
                errors.append(
                    f"richness: fewer than {need} decisions with rationale "
                    f"({len(good_decisions)})")
            if not self.open_loops:
                errors.append("richness: no open_loops")
            if len(self.canary_questions) < RICHNESS_MIN_CANARY:
                errors.append(
                    f"richness: fewer than {RICHNESS_MIN_CANARY} canary "
                    f"questions ({len(self.canary_questions)})")
            for q in self.canary_questions:
                if isinstance(q, dict) and not str(q.get("source_pointer") or "").strip():
                    errors.append(
                        f"canary {q.get('id') or '?'} has no source_pointer — an "
                        f"unpointable fact is not gradeable and was never a fair "
                        f"question")
            if not self.hazards:
                errors.append("richness: no hazards")
            if not self.first_effect:
                errors.append("richness: no first_effect declared")

        return errors

    # --- rendering ---

    def render_md(self) -> str:
        ps = self.phase_state
        lines: List[str] = []

        lines.append("## current_goal")
        lines.append(self.current_goal or "(none)")
        lines.append("")

        lines.append("## phase_state")
        lines.append(f"- plan_ref: {ps.plan_ref}")
        lines.append(f"- phase {ps.phase_n} of {ps.phase_m}")
        lines.append(
            f"- gates_passed: {', '.join(str(g) for g in ps.gates_passed) if ps.gates_passed else '(none)'}"
            f" (of {ps.gates_total})"
        )
        lines.append(f"- current_step: {ps.current_step}")
        lines.append(f"- next_gate: {ps.next_gate}")
        lines.append("")

        lines.append("## working_state")
        lines.append(self.working_state or "(none)")
        lines.append("")

        lines.append("## open_loops")
        if self.open_loops:
            for loop in self.open_loops:
                if is_callback(loop):
                    lines.append(
                        f"- [callback:{loop.get('state', CB_PENDING)}] "
                        f"reply to {loop.get('to')} re "
                        f"{loop.get('what_for')} (req {loop.get('request_id')})")
                else:
                    lines.append(f"- {loop}")
        else:
            lines.append("(none)")
        lines.append("")

        lines.append("## decisions")
        if self.decisions:
            for dec in self.decisions:
                text = dec.get("text", "")
                rationale = dec.get("rationale", "")
                lines.append(f"- {text} — {rationale}" if rationale else f"- {text}")
        else:
            lines.append("(none)")
        lines.append("")

        lines.append("## file_roots_touched")
        if self.file_roots_touched:
            lines.extend(f"- {root}" for root in self.file_roots_touched)
        else:
            lines.append("(none)")
        lines.append("")

        lines.append("## next_3_actions")
        if self.next_3_actions:
            lines.extend(f"{i}. {a}" for i, a in enumerate(self.next_3_actions, 1))
        else:
            lines.append("(none)")

        return "\n".join(lines)
