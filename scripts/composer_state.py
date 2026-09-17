"""composer_state — the ONE robust read of a pane's composer (input) line.

Every reader that needs to know "is there unsubmitted human input in this pane's
composer?" MUST call `read_composer(pane)` here instead of scraping the pane itself.
Before this module, readers each had their own scrape and several used
`tmux capture-pane -p` WITHOUT `-e`, so they stripped the SGR codes and could not see
that a line was a DIM ghost suggestion (an LLM autocomplete/placeholder the runtime
renders in the composer) rather than human-typed text. That is how a Claude ghost line
(a full sentence the model suggested, e.g. "yes example.com is primary now, remove the
old domain from search console") was read as unsubmitted typed input by both a human and
the fleet.

A ghost is NOT human input:
  * never clobber it (it is not work to preserve),
  * never treat it as pending input (do not hold delivery / a swap on it),
  * never Enter it (submitting a ghost sends the LLM's own suggestion as if the human
    typed it).

The classifier is the SGR-dim walk ported from message-router.py:input_line_state (the
one reader that already got this right), keyed on the per-runtime prompt signature from
runtime_signatures.PROMPT_SIGNATURES so the SAME table drives every runtime.

    read_composer(pane) -> {
        "state":   "empty" | "ghost" | "typed" | "submitted" | "unknown",
        "text":    the human-typed text (only when state == "typed", else ""),
        "runtime": the resolved runtime ("claude" | "gemini" | "codex" | "unknown"),
        "evidence": {the composer line, the working marker, why},
    }

State meanings:
  empty     — bare prompt, nothing after it.            safe (no human input)
  ghost     — a DIM suggestion/placeholder after the prompt (LLM text, not human).
              safe (no human input) — but NEVER submit it.
  typed     — default-rendered (non-dim, non-cursor) characters after the prompt =
              a human typed this and has not submitted it.   NOT safe: preserve it.
  submitted — the runtime is mid-turn / working (the composer is not accepting input).
              NOT a swap point; the turn is in flight.
  unknown   — the pane/runtime could not be read honestly (no prompt line, capture
              failed, or an unrecognized runtime). FAIL-CLOSED: callers treat unknown
              like typed/busy — never clobber, never Enter, never assume empty.

An UNKNOWN runtime is NEVER classified 'typed' — we cannot know its ghost rendering, so
we refuse to guess (a wrong 'typed' silently holds the fleet; a wrong 'empty' clobbers
human work — both are the 'success that isn't' class).
"""
import json
import re
import subprocess

from scripts.runtime_signatures import (  # noqa: E402
    ORCHESTRA_DIR, PROMPT_SIGNATURES, resolve_runtime)

DEFAULT_RUNTIME = "claude"

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

# Known EMPTY-composer placeholders each runtime renders (dim). These are the belt to the
# SGR-dim walk's suspenders: on a STRIPPED capture (no -e) the dim styling is gone and the
# walk would read a placeholder as typed, so a positive placeholder match reclassifies it
# 'ghost'. With -e the walk already catches them; this only helps a degraded/stripped read.
_PLACEHOLDER_RES = (
    re.compile(r'^Try ".{0,60}"$'),            # claude
    re.compile(r"^Ask Codex to do anything$"),  # codex
    re.compile(r"^Type your message", re.I),    # gemini / antigravity
)


def _is_placeholder(text):
    return any(r.match(text) for r in _PLACEHOLDER_RES)

# Per-runtime "the turn is in flight" markers (composer not accepting input). Presence of
# any of these on the screen => 'submitted' (working), which outranks the composer line
# read (a working pane may still render the last prompt). Verified by effect on the live
# CLIs (claude 2.1.x, gemini 3.x, codex 0.148+).
_WORKING_MARKERS = {
    "claude": re.compile(r"esc to interrupt|\besc\b.*interrupt", re.I),
    "codex":  re.compile(r"Working \(\d+s|esc to interrupt", re.I),
    # Gemini shows no idle footer while working; handled positively below.
    "gemini": re.compile(r"esc to (cancel|interrupt)", re.I),
}


def _strip_sgr(s):
    return SGR_RE.sub("", s)


def classify_input_line(ansi_line, sig=None):
    """Classify one composer line (WITH its SGR codes intact) as
    'empty' | 'ghost' | 'typed', and return (state, typed_text).

    Ported from message-router.py:input_line_state. Walks the line tracking SGR dim
    ([2m) and reverse ([7m, the cursor block). A visible char that is neither dim nor
    reverse is human-typed. `sig` is the per-runtime signature (defaults to Claude's, so
    the Claude path is byte-identical to the router).
    """
    sig = sig or PROMPT_SIGNATURES[DEFAULT_RUNTIME]
    prompt_char = sig["prompt_char"]
    idx = ansi_line.find(prompt_char)
    if idx == -1:
        return ("unknown", "")  # no prompt on this line
    rest = ansi_line[idx + len(prompt_char):]

    if not sig.get("has_ghost_suggestions", True):
        # No dim ghost class (Gemini): any non-space visible char after the prompt is
        # human-typed. The cursor block wraps a space on an empty prompt -> ignore spaces.
        stripped = _strip_sgr(rest).replace("\xa0", " ")
        typed = stripped.strip()
        return ("typed", typed) if typed else ("empty", "")

    dim = reverse = False
    saw_ghost = saw_typed = False
    typed_chars = []
    pos = 0
    while pos < len(rest):
        m = SGR_RE.match(rest, pos)
        if m:
            codes = m.group(1).split(";") if m.group(1) else ["0"]
            for c in codes:
                if c in ("", "0"):
                    dim = reverse = False
                elif c == "2":
                    dim = True
                elif c == "7":
                    reverse = True
                elif c == "22":
                    dim = False
                elif c == "27":
                    reverse = False
            pos = m.end()
            continue
        ch = rest[pos]
        if not (ch.isspace() or ch == "\xa0"):
            if dim:
                saw_ghost = True
            elif reverse:
                pass  # cursor block
            else:
                saw_typed = True
                typed_chars.append(ch)
        elif saw_typed and not dim and not reverse:
            typed_chars.append(ch)  # preserve interior spaces of typed text
        pos += 1

    if saw_typed:
        typed = "".join(typed_chars).strip()
        # Belt for a stripped (no-SGR) capture: a known placeholder read as 'typed' because
        # its dim styling was stripped is really an empty composer -> 'ghost'.
        if _is_placeholder(typed):
            return ("ghost", "")
        return ("typed", typed)
    if saw_ghost:
        return ("ghost", "")
    return ("empty", "")


def _capture(pane, capture_fn=None):
    """tmux capture-pane -e -p (SGR PRESERVED — the -e is the whole point) for `pane`.
    Injectable for tests. Returns the raw text, or None on any failure (fail-closed)."""
    if capture_fn is not None:
        return capture_fn(pane)
    try:
        out = subprocess.run(
            ["tmux", "capture-pane", "-e", "-p", "-t", str(pane)],
            capture_output=True, text=True, check=False, timeout=5)
        return out.stdout if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 — any capture error is fail-closed 'unknown'
        return None


def _resolve_runtime(pane, runtime):
    """Resolve the pane's DECLARED runtime. An explicit `runtime` wins. Otherwise look the
    pane up in registry.json (by agent-id == pane, or by tmux_session) and resolve_runtime
    it NON-strict. Anything we cannot resolve positively is 'unknown' — NEVER a fail-open
    to 'claude' (gm's contract: an unknown runtime must classify 'unknown', never 'typed')."""
    if runtime:
        return runtime.strip().lower()
    try:
        reg = json.loads((ORCHESTRA_DIR / "registry.json").read_text())
        agents = reg.get("agents", reg) if isinstance(reg, dict) else {}
        entry = agents.get(str(pane))
        if entry is None:  # try matching by tmux_session
            entry = next((e for e in agents.values()
                          if isinstance(e, dict) and e.get("tmux_session") == str(pane)), None)
        rt = resolve_runtime(entry or {}, agent_id=str(pane), strict=False)
        return (rt or "unknown")
    except Exception:  # noqa: BLE001 — any lookup failure is fail-closed 'unknown'
        return "unknown"


def classify_screen(raw, *, runtime=None):
    """Classify an ALREADY-CAPTURED screen (raw text WITH SGR, from `capture-pane -e -p`)
    for a KNOWN runtime. Same {state,text,runtime,evidence} contract as read_composer, but
    the caller supplies the capture — for readers that already hold the pane text (e.g. the
    rotation beat, which captures its own target). `raw` MUST retain SGR (-e); a stripped
    capture cannot see ghosts and will misread them as typed."""
    rt = (runtime or "").strip().lower() or "unknown"
    if not raw:
        return {"state": "unknown", "text": "", "runtime": rt,
                "evidence": {"why": "empty capture", "line": None}}
    sig = PROMPT_SIGNATURES.get(rt)
    if sig is None:
        return {"state": "unknown", "text": "", "runtime": rt,
                "evidence": {"why": "unknown runtime (no signature)", "line": None}}
    wm = _WORKING_MARKERS.get(rt)
    if wm and wm.search(raw):
        return {"state": "submitted", "text": "", "runtime": rt,
                "evidence": {"why": "working marker present", "marker": wm.pattern}}
    prompt_char = sig["prompt_char"]
    composer_line = None
    for line in reversed(raw.split("\n")):
        if prompt_char in _strip_sgr(line):
            composer_line = line
            break
    if composer_line is None:
        return {"state": "unknown", "text": "", "runtime": rt,
                "evidence": {"why": "no prompt line found", "line": None}}
    state, text = classify_input_line(composer_line, sig)
    if state == "unknown":
        return {"state": "unknown", "text": "", "runtime": rt,
                "evidence": {"why": "prompt not locatable in raw line", "line": composer_line}}
    return {"state": state, "text": text if state == "typed" else "", "runtime": rt,
            "evidence": {"line": composer_line, "why": f"classified {state}"}}


def read_composer(pane, *, runtime=None, capture_fn=None):
    """Robust composer read for one pane: resolve runtime, capture with SGR, classify.
    Returns the {state,text,runtime,evidence} dict. NEVER raises — every failure is
    'unknown'."""
    rt = _resolve_runtime(pane, runtime)
    raw = _capture(pane, capture_fn)
    if not raw:
        return {"state": "unknown", "text": "", "runtime": rt,
                "evidence": {"why": "capture failed or empty", "line": None}}
    return classify_screen(raw, runtime=rt)


_GLYPH_RUNTIME = {"❯": "claude", "›": "codex", ">": "gemini"}


def _infer_runtime(raw):
    """Pick a runtime SIGNATURE from the bottom-most prompt glyph when the caller did not
    declare one — ❯→claude, ›→codex, >→gemini. This selects which SGR signature to apply
    (not a typed-vs-ghost guess); '>' maps to the gemini (no-ghost) signature, the
    conservative default (treats any visible char as typed = never drops human input)."""
    for line in reversed((raw or "").split("\n")):
        s = _strip_sgr(line).replace("\xa0", " ").strip()
        for glyph, rt in _GLYPH_RUNTIME.items():
            if s.startswith(glyph) and not s.startswith(">>"):
                return rt
    return None


def composer_text(lines, runtime=None):
    """Compat contract (supersedes the old wal/composer_read.composer_text): the human-TYPED
    composer content, '' when empty/ghost/placeholder/working, None when no prompt line is
    readable. Accepts a list of lines OR a raw string; PREFERS a `-e` (SGR-preserving)
    capture — a stripped capture still works via the placeholder belt but cannot see a
    content-ghost. `runtime` is used when known; otherwise inferred from the prompt glyph."""
    raw = lines if isinstance(lines, str) else "\n".join(lines or [])
    rt = (runtime or "").strip().lower() or _infer_runtime(raw)
    if not rt:
        return None
    r = classify_screen(raw, runtime=rt)
    if r["state"] == "typed":
        return r["text"]
    if r["state"] == "unknown":
        return None
    return ""  # empty | ghost | submitted -> no unsubmitted human text


def settle_and_read(pane, *, send_fn=None, capture_fn=None, runtime=None):
    """STALE-SCREEN rule (memory: a composer probe reads the OLD text until a printable
    key lands). After you have just sent Enter or C-u to `pane`, the next capture can
    still show the pre-key screen. Send a space then a backspace (a no-op printable +
    delete) to force a redraw, then read. Only for callers ACTIVELY DRIVING a pane
    (e.g. rotation readiness after a C-u clear) — never for passive observation, and
    never on a pane a human is attached to.
    """
    send = send_fn or (lambda keys: subprocess.run(
        ["tmux", "send-keys", "-t", str(pane), *keys], check=False))
    try:
        send(["space"])
        send(["BSpace"])
    except Exception:  # noqa: BLE001 — best effort; still read
        pass
    return read_composer(pane, runtime=runtime, capture_fn=capture_fn)
