# voice_guards.py — VQ-9: suppress model-generated re-engagement filler during pauses.
#
# the operator complained TWICE (VQ-3 then VQ-9): during silences Arturo cycles keep-alive chatter like
# "Is there anything specific you'd like me to help you with?". These are MODEL-GENERATED (not the
# proxy's signoff strings), so they slip past the empty-turn silence guard when ASR emits a short
# low-content turn during a pause. the operator wants SILENCE for short pauses. This module detects the
# pause + the re-engagement shape so the caller can suppress the response (yield DONE, no speech).
import json
import re

# Re-engagement / keep-alive phrasings the model reaches for when it has nothing to add. Matched
# on a NORMALIZED (lowercased, punctuation-stripped) response. Kept deliberately tight so a genuine
# answer that merely CONTAINS "let me know" isn't nuked — see is_reengagement_filler's brevity gate.
_REENGAGE_PATTERNS = [
    r"is there anything (specific |else )?(i can|you)",
    r"anything (specific|else) (you|i can)",
    r"how can i (help|assist)",
    r"what can i (help|do|assist)",
    r"what would you like (me )?to (help|do)",
    r"let me know (if|how|what) (i|you)",
    r"i'?m here (if|whenever|to help)",
    r"(just )?let me know (if|when)",
    r"anything (i can|you'?d like)",
    r"is there something",
]
_REENGAGE_RE = re.compile("|".join(f"(?:{p})" for p in _REENGAGE_PATTERNS))


def _norm(t):
    t = (t if isinstance(t, str) else str(t)).lower()
    return re.sub(r"[^a-z0-9\s']", " ", t).strip()


def is_pause_turn(user_text, max_len=4):
    """True if the latest user turn is a PAUSE (nothing meaningful said): empty, an ellipsis, or a
    very short low-signal utterance (<= max_len chars after strip — 'ok', 'mm', 'uh', '...')."""
    s = (user_text or "").strip()
    if not s or s in ("...", ".", "..", "…"):
        return True
    return len(s) <= max_len


def is_reengagement_filler(text, max_words=16):
    """True if `text` is a BARE re-engagement/keep-alive phrase (matches a known pattern AND is
    short — a real answer that happens to end with 'let me know if...' is longer, so the word cap
    protects it). Used only in combination with is_pause_turn (double-gate) so genuine content is
    never suppressed."""
    s = _norm(text)
    if not s:
        return False
    if len(s.split()) > max_words:
        return False                       # long response = real content, not a bare keep-alive
    return bool(_REENGAGE_RE.search(s))


def should_suppress_reengagement(user_text, response_text):
    """VQ-9 double-gate: suppress iff the user turn is a pause AND the response is a bare
    re-engagement filler. Prefer SILENCE for short pauses (the operator ruling)."""
    return is_pause_turn(user_text) and is_reengagement_filler(response_text)


# --- BUG-3 (app-dev-v4, call vc_2fb009f8): duplicate USER turns Arturo double-answered ----------
# Evidence: ElevenLabs' endpointer emitted the SAME utterance twice with tiny variation ("...from.
# And why..." vs "...from, and why...", ratio 1.0; "meeting ... tomorrow" vs "... today", ratio
# 0.94) — SAME LENGTH, so VQ-6's is_partial_of (which required the full turn to be strictly LONGER)
# never matched, and when the dup arrived AFTER the first answer had streamed there was no in-flight
# entry to cancel anyway. This adds (a) a symmetric near-duplicate test and (b) a stateless
# "already-answered duplicate" detector for the SEQUENTIAL case VQ-6 structurally can't reach.
import difflib as _difflib
from services.arturo.call_journal import _norm_fuzzy as _nf


def is_near_duplicate(a, b, ratio_thresh=0.9, max_extra_tokens=2, min_tokens=4):
    """True if `a` and `b` are the SAME utterance re-transcribed (order-independent): high difflib
    ratio AND the longer has at most max_extra_tokens content tokens beyond the shorter. The
    extra-token cap distinguishes a re-transcription ("...from." vs "...from,") from a genuine
    EXTENSION that adds real content ("...from" -> "...from and why did you send 73 texts", many
    tokens — NOT a duplicate). The min_tokens floor (AGY congruence review): SHORT turns ("yes",
    "no", "do it") are NOT deduped — the operator legitimately repeats short confirmations, and this guard
    is the SEQUENTIAL suppressor (it must never silence a genuine short re-ask). Genuinely
    concurrent short dups are still handled by VQ-6's in-flight cancel, not here."""
    ta, tb = _nf(a), _nf(b)
    if not ta or not tb:
        return False
    if min(len(ta), len(tb)) < min_tokens:
        return False                          # too short to safely call a duplicate
    if abs(len(ta) - len(tb)) > max_extra_tokens:
        return False
    # extra content tokens (symmetric difference of the multiset-ish token sets)
    extra = len(set(ta) ^ set(tb))
    if extra > max_extra_tokens * 2:          # each swapped word contributes up to 2 to symdiff
        return False
    return _difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= ratio_thresh


def latest_is_answered_duplicate(messages, lookback=12):
    """Stateless SEQUENTIAL-duplicate guard: return True if the LATEST user turn is a near-duplicate
    of an EARLIER user turn (within the last `lookback` messages) that was ALREADY answered (has an
    assistant turn after it). That means Arturo already responded to this utterance and ElevenLabs
    re-sent it — so the caller should SUPPRESS the re-answer. `messages`: the OpenAI-style history
    (list of {role, content}). Only considers voice-relevant user/assistant roles."""
    # find the latest user turn + its index
    latest_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user" and (messages[i].get("content") or "").strip():
            latest_idx = i
            break
    if latest_idx is None:
        return False
    latest = messages[latest_idx]["content"]
    start = max(0, latest_idx - lookback)
    for j in range(start, latest_idx):
        m = messages[j]
        if m.get("role") != "user" or not (m.get("content") or "").strip():
            continue
        if not is_near_duplicate(latest, m["content"]):
            continue
        # was that earlier occurrence already answered? (an assistant turn between j and latest_idx)
        if any(messages[k].get("role") == "assistant" and (messages[k].get("content") or "").strip()
               for k in range(j + 1, latest_idx)):
            return True
    return False


def latest_is_answered_superset(messages, lookback=12, min_tokens=4):
    """Hume-final SUPERSET guard (ios msg_7293955c, wrist call BBE3CF65): Hume emits multiple
    interim:false finals for ONE utterance as growing supersets. is_near_duplicate deliberately
    rejects extensions (max_extra_tokens), so BUG-3 misses them and the same sentence gets
    re-answered + journaled per final. True if the LATEST user turn EQUALS or EXTENDS
    (normalized-prefix) an EARLIER user turn that was ALREADY answered — the caller suppresses
    the re-answer. Short turns (< min_tokens) are never deduped (same rule as BUG-3)."""
    latest_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user" and (messages[i].get("content") or "").strip():
            latest_idx = i
            break
    if latest_idx is None:
        return False
    latest_n = " ".join(_nf(messages[latest_idx]["content"]))
    if len(latest_n.split()) < min_tokens:
        return False
    start = max(0, latest_idx - lookback)
    for j in range(start, latest_idx):
        m = messages[j]
        if m.get("role") != "user" or not (m.get("content") or "").strip():
            continue
        prev_n = " ".join(_nf(m["content"]))
        if len(prev_n.split()) < min_tokens:
            continue
        if latest_n != prev_n and not latest_n.startswith(prev_n + " "):
            continue
        if any(messages[k].get("role") == "assistant" and (messages[k].get("content") or "").strip()
               for k in range(j + 1, latest_idx)):
            return True
    return False


_THOUGHT_MARKER_RE = re.compile(r"^\s*(?:\*\*)?thought(?:\*\*)?:?\s*\n", re.IGNORECASE)


def strip_thought_block(text):
    """Strip a leaked model-reasoning block (ios msg_7293955c: a spoken reply began
    'thought\\nThe user is reporting...' — Gemini's reasoning channel leaked into content).
    Only fires when the text STARTS with a bare 'thought' marker line ('I thought about it'
    passes through). Multi-paragraph: keep ONLY the final paragraph (the actual answer).
    Single paragraph = pure reasoning: return '' — a silent turn beats spoken chain-of-thought."""
    if not text:
        return text
    m = _THOUGHT_MARKER_RE.match(text)
    if not m:
        return text
    paras = [p for p in re.split(r"\n\s*\n", text[m.end():]) if p.strip()]
    if len(paras) >= 2:
        return paras[-1].strip()
    return ""


_TICKED_SENTENCE_RE = re.compile(r"\s*[^.!?]*`[^`]+`[^.!?]*[.!?]")


def strip_leading_tool_reasoning(text):
    """Thought-leak VARIANT (ios msg_72c19e09): reasoning glued to the answer with NO
    'thought' marker — e.g. 'Therefore, the `ask_gm` tool is appropriate... I will combine
    both requests into a single `ask_gm` call.On it, I'll text...'. A voice reply never
    legitimately SPEAKS backticked identifiers, so leading sentences containing a backticked
    token are reasoning: strip them until the first backtick-free sentence (which is the
    answer). All-reasoning => '' (silence beats spoken tool-planning). Handles the glued
    'call.On it' case because the match ends at the period regardless of spacing."""
    if not text or "`" not in text:
        return text
    out, n = text, 0
    while True:
        m = _TICKED_SENTENCE_RE.match(out)
        if not m:
            break
        out = out[m.end():].lstrip()
        n += 1
    return out if n else text


# --- Option-1 model-filler sanitizer (commission msg_b82abb4f) ----------------
# Root cause LOG-PROVEN (pocket-transcript-miner msg_48ce6fa2): gemini-2.5-flash
# AUTHORS stacked canned-filler barrages IN CONTENT, learned from weeks of
# filler-laden ElevenLabs history replay (contagion). The proxy's VQ-3/3b gates
# only govern proxy-side emission and were working (emitted=False throughout the
# offending call). Fix: strip exact pool sentences at content START before SSE
# (proxy gate becomes the SOLE filler authority) + scrub the same pool from
# assistant history so the contagion starves and history self-cleans.
#
# CONSERVATIVE by design: exact sentences from the known pool only (the live
# _filler_pool six + the two historical variants still in logged barrages),
# start-anchored, sentence-boundary-checked, never mid-content.
FILLER_SENTENCES = ("One sec.", "Give me a moment.", "Hang on.", "Still on it.",
                    "Almost there.", "Bear with me.", "Pulling that up.",
                    "Checking now.",
                    # Q0 addendum item 1 (the operator field test vc_client_1e38de794d65,
                    # gm msg_599e3219): "On it."/"Got it." joined the contagion —
                    # appearing in model content and doubling with the platform's
                    # spoken filler. Trailing-variant forms included.
                    "On it.", "On it!", "Got it.", "Got it!")
_FILLER_PREFIX_RE = re.compile(
    r'^(?:' + '|'.join(re.escape(s) for s in FILLER_SENTENCES) + r')(?=\s|$)',
    re.IGNORECASE)


def _strip_stacked(content):
    """Core loop: repeatedly strip a leading pool sentence. Returns (out, n)."""
    out, n = content, 0
    while True:
        m = _FILLER_PREFIX_RE.match(out.lstrip())
        if not m:
            break
        s = out.lstrip()
        out = s[m.end():].lstrip()
        n += 1
    return out, n


def strip_leading_fillers(content):
    """Strip STACKED exact canned-filler sentences at content START (live SSE
    path). Returns (content, n_stripped). Fail-safe: a reply that is ONLY
    fillers collapses to exactly ONE ack (never an empty spoken turn) —
    matching the VQ-3 one-filler doctrine."""
    if not isinstance(content, str) or not content:
        return content, 0
    out, n = _strip_stacked(content)
    if n and not out:
        first = _FILLER_PREFIX_RE.match(content.lstrip())
        return content.lstrip()[:first.end()], n - 1
    return out, n


def _could_extend_to_filler(residue):
    """True if `residue` (head text after stripping complete fillers) is still a
    strict PREFIX of some pool sentence — i.e. more stream could complete it."""
    r = residue.lstrip().lower()
    return bool(r) and any(s.lower().startswith(r) for s in FILLER_SENTENCES)


class LeadingFillerStreamGate:
    """Streaming head-gate for the SSE tool-loop paths, where model content
    arrives chunk-by-chunk and a canned filler can SPAN chunk boundaries.

    Buffers ONLY the stream head (bounded: at most one pool-sentence length
    beyond complete fillers, ~17 chars) until the leading filler run is fully
    consumed, then passes everything through verbatim. flush() at end-of-stream
    applies the keep-one-ack fail-safe for an all-filler turn."""

    def __init__(self):
        self._buf = ""
        self._passed = False
        self.stripped = 0

    def feed(self, piece):
        if self._passed:
            return piece
        self._buf += piece
        out, n = _strip_stacked(self._buf)
        if not out or _could_extend_to_filler(out):
            return ""                     # still inside (or possibly inside) the head run
        self._passed = True
        self.stripped = n
        self._buf = ""
        return out

    def flush(self):
        if self._passed or not self._buf:
            return ""
        out, n = strip_leading_fillers(self._buf)   # keep-one-ack fail-safe
        self.stripped = n
        self._buf = ""
        self._passed = True
        return out


# --- leg-3 pacing heartbeat (the operator's verbatim cadence, msg_b82abb4f item 3) ----
# "one ack, let it breathe, then only every ~8-10s while genuinely still
# working." The ack at round start is the existing FILLER_GATE's job; this
# class paces INFORMATIVE progress lines DURING a long tool execution.
# Templates deliberately do NOT start with sanitizer-pool sentences; they self-
# clean from replayed history via scrub_history (contagion round-2 guard).
HEARTBEAT_PREFIXES = ("Still working —", "Working on it —", "Hang tight —")
_HEARTBEAT_TEMPLATES = ("Still working — {tool} is running.",
                        "Working on it — {tool} hasn't finished yet.",
                        "Hang tight — still waiting on {tool}.")


def _format_dynamic_pulse(tool_name, tool_args, tool_names_str, emitted_idx):
    """Generate dynamic, context-aware progress updates for long-running tools.

    Rotates through distinct, informative progress lines describing what the tool
    is actively doing (e.g. querying gemini-gm / deep brain, reviewing agent states,
    or executing shell commands), while keeping the WebRTC downlink alive.
    """
    tn = (tool_name or "").lower()
    tool_label = tool_name or tool_names_str or "the tool"
    args = tool_args or {}

    if tn in ("gm_command", "async_task") or "gm" in tn:
        prompt = str(args.get("prompt", "") or args.get("summary", "")).strip()
        short_p = f" on '{prompt[:35]}...'" if len(prompt) > 8 else ""
        templates = [
            f"Working on it — {tool_label} is querying the deep brain{short_p}.",
            f"Still working — {tool_label} is synthesizing the analysis.",
            f"Hang tight — {tool_label} is pulling the final breakdown together.",
            f"Still working — {tool_label} is wrapping up with the deep brain.",
        ]
    elif tn in ("list_agents", "get_agent_output", "agent_status", "read_agent_conversation"):
        sess = str(args.get("session_id", "") or args.get("name", "") or args.get("agent_id", "")).strip()
        sess_str = f" for {sess}" if sess else ""
        templates = [
            f"Working on it — {tool_label} is checking the fleet status{sess_str}.",
            f"Still working — {tool_label} is pulling the latest agent output.",
            f"Hang tight — {tool_label} is reviewing the conversation logs.",
        ]
    elif tn in ("run_command", "run_script"):
        cmd = str(args.get("cmd", "") or args.get("command", "")).strip()
        cmd_str = f" on '{cmd[:30]}...'" if len(cmd) > 5 else ""
        templates = [
            f"Working on it — {tool_label} is running the command{cmd_str}.",
            f"Still working — {tool_label} is waiting for execution to finish.",
            f"Hang tight — {tool_label} is processing the command output.",
        ]
    elif tn in ("query_roadmap", "list_features", "roadmap_context"):
        templates = [
            f"Working on it — {tool_label} is searching the roadmap records.",
            f"Still working — {tool_label} is cross-referencing project features.",
            f"Hang tight — {tool_label} is compiling the roadmap updates.",
        ]
    elif tn in ("query_memory", "recall_facts"):
        templates = [
            f"Working on it — {tool_label} is querying memory records.",
            f"Still working — {tool_label} is cross-referencing past context.",
            f"Hang tight — {tool_label} is retrieving the details.",
        ]
    else:
        templates = [
            f"Working on it — {tool_label} is running.",
            f"Still working — {tool_label} hasn't finished yet.",
            f"Hang tight — still waiting on {tool_label}.",
            f"Still working — {tool_label} is almost complete.",
        ]
    return templates[emitted_idx % len(templates)]


class ToolHeartbeat:
    """Per-TURN pacing policy (pure; the proxy owns threading/yield).

    First ping: only after `interval_s` of continuous execution AND (if given)
    the VQ-3b cross-turn cooldown gate `allowed_fn(now)` — which records the
    emit, so a post-turn filler won't immediately follow. Subsequent pings pace
    at `interval_s`, are NOT re-gated (the operator explicitly asked for the cadence),
    rotate distinct dynamic informative templates, and stop at `budget`."""

    def __init__(self, tool_names, *, interval_s=5.0, budget=8,
                 allowed_fn=None, start_ts=None):
        import time as _t
        self._tool_names = list(tool_names) if isinstance(tool_names, (list, tuple)) else [str(tool_names)]
        self._tools = ", ".join(self._tool_names) or "the tool"
        self._interval = float(interval_s)
        self._budget = int(budget)
        self._allowed_fn = allowed_fn
        self._last = float(start_ts) if start_ts is not None else _t.time()
        self._emitted = 0

    def maybe_ping(self, now, tool_name=None, tool_args=None):
        if self._emitted >= self._budget:
            return None
        if now - self._last < self._interval:
            return None
        if self._emitted == 0 and self._allowed_fn is not None \
                and not self._allowed_fn(now):
            return None
        t_name = tool_name or (self._tool_names[0] if self._tool_names else "the tool")
        line = _format_dynamic_pulse(t_name, tool_args, self._tools, self._emitted)
        self._emitted += 1
        self._last = now
        return line


_HEARTBEAT_PREFIX_RE = re.compile(
    r'^(?:' + '|'.join(re.escape(p) for p in HEARTBEAT_PREFIXES)
    + r')[^.]*\.\s*')


def _strip_heartbeat_lines(content):
    """Strip leading proxy heartbeat sentences (history hygiene)."""
    out, n = content, 0
    while True:
        m = _HEARTBEAT_PREFIX_RE.match(out.lstrip())
        if not m:
            break
        out = out.lstrip()[m.end():].lstrip()
        n += 1
    return out, n


def scrub_history(messages):
    """Strip canned fillers AND proxy heartbeat lines from ASSISTANT-role
    history content before it goes to the model (starves the contagion; the
    heartbeat strip is the round-2 guard so pacing lines can't seed a new
    learned pattern). FULL strip — an emptied assistant turn stays empty in
    history. user/system/tool entries and non-str content are never touched.
    Returns a NEW list; input is not mutated."""
    out = []
    for m in messages:
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            clean, n1 = _strip_stacked(m["content"])
            clean, n2 = _strip_heartbeat_lines(clean)
            if n2:
                # heartbeats can precede a residual filler run — one more pass
                clean, n3 = _strip_stacked(clean)
            if n1 or n2:
                if not clean.strip() and not m.get("tool_calls"):
                    # P0 FIELD REGRESSION (the operator live call 2026-08-18 05:02): an
                    # emptied assistant turn left as content:"" taught Gemini
                    # the speech channel was dead — it answered the operator via
                    # send_telegram with confused narration. An all-filler turn
                    # is DROPPED entirely; the model must never see a blank
                    # assistant turn.
                    continue
                m = {**m, "content": clean}
        out.append(m)
    return out


# --- Q0: mechanical send_telegram appropriateness gate (gm msg_6f4f2050) -------
# the operator ruling after the 2026-08-18 misroute ("I said how's it going and you
# replied with hey"): internal/system messages must NEVER go to the operator on Telegram
# unless (a) the operator explicitly asked for a text, or (b) the message carries a
# clickable deliverable. The tool DESCRIPTION already said this and the model
# ignored it — prompt-level guidance is proven insufficient; this is MECHANISM.
#
# The deny result TEACHES (BUG-1 lesson inverted): it must not trigger a retry
# loop, but unlike the anti-spam guard it must NOT claim the send happened —
# the model needs to know to SPEAK.
TG_DENY_MESSAGE = ("Not sent — the operator has not asked for a text and this contains no "
                   "deliverable link. SPEAK your reply instead. (Genuinely urgent "
                   "proactive alerts go through gm_command — gm decides and texts.)")

_TG_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# Conservative texting-intent matcher. Err toward ALLOW whenever the operator used the
# word "text"/"telegram" at all; "message/dm me" needs the me-directed form so
# "send it to the gm" (inject_message intent) never reads as a the operator-text ask.
_TG_INTENT_RE = re.compile(
    r"\btext(s|ed|ing)?\b"
    r"|\btelegram\b"
    r"|\b(message|msg|dm)\s+(it\s+to\s+)?me\b"
    r"|\bsend\s+me\b"
    r"|\bsend\s+(it|that|this)\s+to\s+me\b",
    re.IGNORECASE)


def tg_send_appropriate(message, recent_user_turns, lookback=5):
    """Mechanical appropriateness gate for send_telegram. Returns (ok, reason):
    ok=True with reason 'deliverable_link' or 'explicit_request', else
    (False, TG_DENY_MESSAGE). Fail-CLOSED: no turns/None => deny unless the
    message itself carries an http(s) link."""
    if _TG_URL_RE.search(message or ""):
        return True, "deliverable_link"
    turns = [t for t in (recent_user_turns or []) if isinstance(t, str)]
    for t in turns[-lookback:]:
        if _TG_INTENT_RE.search(t):
            return True, "explicit_request"
    return False, TG_DENY_MESSAGE


# --- voice duplicate-inject (gm msg_e290c9bd / re-commissioned msg_c74d3ae3) --
# the operator watched Arturo inject the SAME message 3x into v2's live pane. Root at
# source: the tool loop runs up to MAX_TOOL_ROUNDS and executes whatever
# tool_calls the model returns each round, with NO dedup — a model that repeats
# an identical side-effecting call re-executes it, once per round. Log evidence:
# exact-duplicate inject_message args appear 2x for three distinct messages.
#
# Rule: a SIDE-EFFECTING tool runs at most ONCE per turn per identical
# (name, args). Read-only tools may repeat freely — re-reading is how a model
# refines an answer, and suppressing that would degrade real work.
SIDE_EFFECTING_TOOLS = frozenset({
    "inject_message", "send_telegram", "spawn_agent", "kill_agent",
    "agent_message", "async_task", "run_command", "remember_note",
    "answer_menu", "focus_entity",
})


def is_side_effecting(tool_name: str) -> bool:
    return tool_name in SIDE_EFFECTING_TOOLS


# LIFECYCLE tools act on a NAMED thing, so two calls naming the same thing are the same act even
# when the rest of the prose differs. Everything else keeps exact-args semantics: two different
# messages to one seat, or two different shell commands, are legitimately distinct in one turn
# (congruence DEC-1790305211739337, both peers independently).
_IDENTITY_FIELDS = {
    "spawn_agent": ("session_name", "machine"),
    "kill_agent": ("session_name",),
}

# Omitted args must be normalised to the DEFAULT THE HANDLER APPLIES before hashing, or one call
# omitting `machine` and one passing machine="vps" are the same seat under two different keys
# (arturo-proxy.py: args.get("machine", "vps")).
_ARG_DEFAULTS = {
    "spawn_agent": {"machine": "vps"},
}

# An opposing lifecycle call invalidates the identity: spawn X -> kill X -> spawn X in one turn is
# TWO legitimate spawns, and replaying "running" for a seat that was just killed is worse than a
# duplicate spawn.
_OPPOSES = {
    "kill_agent": ("spawn_agent",),
    "spawn_agent": ("kill_agent",),
}

# Every suppression message carries this marker, so record() can recognise a REPLAY handed back to
# it and refuse to store it as a result. Without that, a caller that records the suppressed call
# overwrites the stored first result with a message quoting itself, and by the fourth reworded call
# the real result has been pushed past the 300-char cap — so "a retry returns the first result"
# (gm's rule) silently stops holding at the three-call shape this guard exists for.
_GUARD_MARKER = "(duplicate-call guard)"


class ToolDedupLedger:
    """Per-TURN record of executed side-effecting calls. Constructed fresh for
    each request, so a later turn may legitimately repeat a call.

    Two keys per call. The EXACT-ARGS key is the original rule. The IDENTITY key is narrower and
    exists only for lifecycle tools, because a model that rewords the same request is not making a
    new one. `reserve()` claims both AT CHECK TIME: the proxy checks every tool_call in a round
    before any result is recorded, so a record-after-execute ledger let same-round twins through.
    """

    def __init__(self):
        self._done = {}          # key -> result string (completed)
        self._inflight = set()   # keys claimed by reserve() but not yet recorded

    @staticmethod
    def _key(name, args):
        try:
            return f"{name}|{json.dumps(args, sort_keys=True, default=str)}"
        except (TypeError, ValueError):
            return f"{name}|{str(args)}"

    @staticmethod
    def _unwrap(name, args):
        """async_task is indirect: its identity is the INNER tool's. A direct spawn and an
        async-wrapped spawn of one seat must collide."""
        if name == "async_task" and isinstance(args, dict):
            inner = args.get("tool_name")
            inner_args = args.get("tool_args")
            if isinstance(inner, str) and inner and isinstance(inner_args, dict):
                return inner, inner_args
        return name, args

    @classmethod
    def _identity_key(cls, name, args):
        """None when this tool has no identity dimension, or the naming field is absent (then the
        exact-args rule still applies — a call must never bypass the ledger entirely)."""
        name, args = cls._unwrap(name, args)
        fields = _IDENTITY_FIELDS.get(name)
        if not fields or not isinstance(args, dict):
            return None
        defaults = _ARG_DEFAULTS.get(name, {})
        parts = []
        for f in fields:
            v = args.get(f, defaults.get(f))
            if v is None:
                return None          # cannot identify it; fall back to exact args
            parts.append(f"{f}={v}")
        return f"id:{name}|" + "|".join(parts)

    def reserve(self, name, args):
        """Claim this call for the turn. Returns None when the caller should EXECUTE, or a message
        for the model when it must not. Call this instead of check() at the point of decision."""
        if not is_side_effecting(name):
            return None
        ident = self._identity_key(name, args)
        exact = self._key(name, args)

        for key, by_identity in ((ident, True), (exact, False)):
            if key is None:
                continue
            if key in self._done:
                prior = self._done[key]
                if by_identity:
                    # The operator-visible half: say plainly that the later wording did not run,
                    # or the model narrates several queued tasks when one happened.
                    return (f"Already done this turn for the same target — this second request was "
                            f"NOT delivered and nothing was repeated (duplicate-call guard). "
                            f"The first result stands: {prior}")
                return (f"Already done this turn — not repeated (duplicate-call guard). "
                        f"Previous result: {prior}")
            if key in self._inflight:
                return ("Already in flight this turn for the same target — this second request was "
                        "NOT delivered and nothing was repeated (duplicate-call guard).")

        if ident is not None:
            self._inflight.add(ident)
        self._inflight.add(exact)
        return None

    def check(self, name, args):
        """Back-compat read-only probe. Prefer reserve(), which also claims the key so a
        same-round twin cannot slip between check and record."""
        if not is_side_effecting(name):
            return None
        for key, by_identity in ((self._identity_key(name, args), True), (self._key(name, args), False)):
            if key is not None and key in self._done:
                prior = self._done[key]
                if by_identity:
                    return (f"Already done this turn for the same target — this second request was "
                            f"NOT delivered and nothing was repeated (duplicate-call guard). "
                            f"The first result stands: {prior}")
                return (f"Already done this turn — not repeated (duplicate-call guard). "
                        f"Previous result: {prior}")
        return None

    def record(self, name, args, result):
        if not is_side_effecting(name):
            return
        if _GUARD_MARKER in str(result):
            # A SUPPRESSED call's "result" IS the replay message. Storing it would clobber the first
            # result it exists to repeat. Fail-safe: the worst case of skipping a record is one extra
            # dispatch later, never a wrong suppression.
            return
        ident = self._identity_key(name, args)
        exact = self._key(name, args)
        self._done[exact] = str(result)[:300]
        self._inflight.discard(exact)
        if ident is not None:
            self._done[ident] = str(result)[:300]
            self._inflight.discard(ident)
        # An opposing lifecycle act clears the other's claim on this identity.
        inner_name, inner_args = self._unwrap(name, args)
        self._invalidate_opposed(inner_name, inner_args)

    def _invalidate_opposed(self, acting_name, acting_args):
        """Clear the opposing lifecycle tool's claim on the identity this call just acted on.

        The acting tool's args may not carry every field the OPPOSED tool keys on: kill_agent names
        no machine, while spawn_agent keys on (session_name, machine). Normalising the absent field
        to its default would clear only the default machine's key, so spawn-on-mac -> kill ->
        spawn-on-mac left the mac key claimed and wrongly suppressed a legitimate re-spawn (peer NIT,
        congruence round 2 of DEC-1790305211739337). So match on the fields we actually have and
        WILDCARD the rest.
        """
        if not isinstance(acting_args, dict):
            return
        for opposed in _OPPOSES.get(acting_name, ()):
            fields = _IDENTITY_FIELDS.get(opposed) or ()
            parts = []
            for f in fields:
                if f not in acting_args:
                    break                      # wildcard from this field on
                parts.append(f"{f}={acting_args[f]}")
            if not parts:
                continue
            prefix = f"id:{opposed}|" + "|".join(parts)
            # Fully specified -> one exact key. Partially specified -> every key under that prefix.
            if len(parts) == len(fields):
                matches = [prefix]
            else:
                matches = [k for k in list(self._done) + list(self._inflight)
                           if k.startswith(prefix + "|")]
            for key in matches:
                self._done.pop(key, None)
                self._inflight.discard(key)


import hashlib as _hashlib
import threading as _threading


_TOOLCODE_FENCE_RE = re.compile(r"```[^\n`]*\n?.*?```", re.DOTALL)
_TOOLCODE_BARE_RE = re.compile(r"(?im)^[ \t]*(?:tool_code\b|print\s*\(\s*default_api\b|default_api\.).*$")


def strip_tool_code(text):
    """Strip Gemini-style tool_code / tool-call syntax from a spoken+journaled reply so it never
    reaches the operator's transcript or TTS (build vc_client_981fecb4fcbf: a ```tool_code print(default_api
    ...) block leaked into the journal as message text). Removes fenced blocks that are tool syntax
    (tool_code/tool_outputs/tool_call) OR contain default_api, plus bare tool_code / print(default_api
    lines. Preserves legit non-tool code fences (a voice reply almost never has one, but don't eat it)."""
    if not text:
        return text
    def _fence(m):
        block = m.group(0)
        head = block[:48].lower()
        if any(k in head for k in ("tool_code", "tool_outputs", "tool_call")) or "default_api" in block:
            return ""
        return block
    out = _TOOLCODE_FENCE_RE.sub(_fence, text)
    out = _TOOLCODE_BARE_RE.sub("", out)
    # collapse blank runs left behind
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def request_signature(messages):
    """Stable signature of a chat request, keyed on its LATEST user turn (normalized) + the number
    of prior user turns (position in the call). Identical ElevenLabs re-sends (same messages) share a
    signature; a distinct utterance OR a later point in the call yields a different one. Empty string
    when there is no user turn to key on (never dedup those)."""
    if not messages:
        return ""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return ""
    latest = _norm(user_msgs[-1].get("content") or "")
    if not latest:
        return ""
    basis = f"{len(user_msgs)}|{latest}"
    return _hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


class RecentRequestGuard:
    """Cross-request dedup (Finding-2, build-162 2026-09-06): suppress an IDENTICAL chat request
    (same request_signature) seen within `ttl` seconds. Closes the gap that the per-turn
    ToolDedupLedger and latest_is_answered_duplicate leave when ElevenLabs rapidly RE-SENDS the same
    request (same history) BEFORE Arturo's answer is appended — which otherwise re-runs generate()
    and DOUBLE-EXECUTES side-effecting tools (the incident: inject_message ran twice). Thread-safe:
    the dev server is threaded and the resends can overlap. First sight records + proceeds; a repeat
    within ttl is a duplicate; after ttl a genuine re-ask proceeds again."""

    def __init__(self, ttl=15.0):
        self.ttl = ttl
        self._seen = {}
        self._lock = _threading.Lock()

    def is_duplicate(self, sig, now):
        if not sig:
            return False
        with self._lock:
            self._seen = {s: t for s, t in self._seen.items() if now - t < self.ttl}  # prune expired
            prior = self._seen.get(sig)
            is_dup = prior is not None and (now - prior) < self.ttl
            # Record ONLY on the proceeding (first/expired) sight — NOT on a duplicate hit. Refreshing
            # on every hit would make the TTL a SLIDING window: a client that keeps resending an
            # identical request every <ttl (exactly what it does when it is NOT getting a response)
            # would never let the signature expire -> permanent suppression = dead air. Recording on
            # first-sight-only fixes the window from the first sight, so a sustained storm still gets
            # ONE proceed every `ttl`, never permanent silence. (Concurrency guarantee is unaffected:
            # it comes from record-on-first-sight UNDER the lock — exactly one overlapping caller sees
            # prior is None and records; the rest are suppressed.)
            if not is_dup:
                self._seen[sig] = now
            return is_dup

    def evict(self, sig):
        """Wire-time hardening: after a PROCEEDING request's generate/dispatch FAILS, evict its
        signature so a legit retry within the TTL is not blackholed (the signature is committed at
        arrival — necessary for the concurrent case — so a failed lead must release it)."""
        if not sig:
            return
        with self._lock:
            self._seen.pop(sig, None)


def latest_user_text(messages):
    """Text of the LAST non-empty user turn in an OpenAI-style history ('' if none)."""
    for m in reversed(messages or []):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"]
    return ""


class AnsweredFinalMemory:
    """Server-held last-ANSWERED user final per conversation (ios 221 grade msg_3a5b4d4b,
    call vc_13d96b7a5d1f6c6b). Hume keeps a WINDOWED history and RETRIES a request that got
    an empty SSE (double-POST -> F2 empty -> ~57s retry with the IDENTICAL 21-message
    history), so the earlier answered pair never appears in the history and every
    history-scanning guard (BUG-3, superset) is structurally blind. This keys on OUR memory
    instead: a latest user final that normalizes identical to the final we LAST ANSWERED on
    this conversation is a repeat REGARDLESS OF AGE, until a DIFFERENT final is answered.
    min_tokens floor (same rule as BUG-3): short confirmations are never deduped — the operator
    legitimately repeats those and each deserves its answer."""

    def __init__(self, min_tokens=4, cap=512):
        self._m = {}
        self._lock = _threading.Lock()
        self.min_tokens = min_tokens
        self.cap = cap

    def _key_text(self, text):
        toks = _nf(text or "")
        if len(toks) < self.min_tokens:
            return None
        return " ".join(toks)

    def is_answered_repeat(self, conversation_id, text):
        n = self._key_text(text)
        if not conversation_id or n is None:
            return False
        with self._lock:
            return self._m.get(conversation_id) == n

    def record_answered(self, conversation_id, text):
        n = self._key_text(text)
        if not conversation_id or n is None:
            return
        with self._lock:
            if len(self._m) >= self.cap:
                self._m.clear()          # bounded; a rare reset only widens the guard briefly
            self._m[conversation_id] = n

    def forget(self, conversation_id):
        with self._lock:
            self._m.pop(conversation_id, None)
