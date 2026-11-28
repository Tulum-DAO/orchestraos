"""The real cmt-v2 extractor: one headless `claude -p` per call (design §2).

Widens the miner's cmt-v1 prompt (pocket-agent/commitment_miner.py) to both directions: what Shaw
committed to or intends, and what the other participants promised Shaw or his side. The output
is NOT trusted here; extract.ground() does quote and speaker grounding afterwards. This module
only gets the model's JSON, checks its shape, and keeps failure distinct from "no items":

    Extraction(ok=False, ...)            -> the call could not be read (retry later)
    Extraction(ok=True, raw={"items": []}) -> genuinely nothing on the call

The subprocess is injected (`runner`) so tests never reach the real CLI.
"""
import json
import os
import re
import subprocess

from postcall.extract import Extraction

PROMPT_VERSION = "cmt-v3"
MAX_TRANSCRIPT_CHARS = 60_000
_OWNERS = ("shaw", "participant")
_PREFIX = ["nice", "-n", "19", "taskset", "-c", "6,7"]  # niced and pinned, like the rest of postcall

PROMPT = f"""[prompt {PROMPT_VERSION}] You extract IMMEDIATE MICRO-COMMITMENTS from this \
conversation transcript. Capture BOTH directions:
- what SHAW committed to do, or intends to do, soon (owner "shaw");
- what the OTHER participants promised Shaw or his side (owner "participant").

Rules:
- Skip promises between two other people that do not involve Shaw or his side.
- Skip pleasantries ("talk soon", "let's catch up"), long-term goals and hypotheticals.
- The quote MUST be copied verbatim from the transcript, from ONE single speaker turn. Never \
stitch two turns together and never paraphrase.
- kind "intention" is only allowed for owner "shaw"; a participant's item is always "commitment".
- agent_work is one line naming a draft, research or internal artefact an agent could produce. \
An agent NEVER sends anything as Shaw.
- Shaw is one of the diarized speakers in EVERY recording. Your job is to find WHICH label is Shaw; never decide that Shaw is absent. Shaw's commitments \
come from his label's turns; every other label is a counterpart.
- Identify which diarization label is Shaw and quote, verbatim from Shaw's own turn, the sentence \
that proves it (a self-introduction, or being addressed by name). If no sentence proves it, still \
give your best label but leave shaw_label_evidence empty; never quote a sentence that does not prove it.

Return ONLY minified JSON, no prose and no code fences:
{{"shaw_label":"<diarization label that is Shaw, e.g. SPEAKER_00>","shaw_label_evidence":"<verbatim \
sentence from Shaw's own turn proving it is Shaw>","items":[{{"owner":"shaw|participant",\
"kind":"commitment|intention","counterpart":"name as spoken or empty","project":"",\
"action_type":"one of email,message,calendar_invite,document_send,review,follow_up",\
"operation":"draft|send|do|review|schedule|decide","deliverable":"short normalized noun phrase",\
"commitment_text":"short action item","timeframe":"temporal phrase as spoken or empty",\
"explicit_date":"date words as spoken or empty","quote":"EXACT verbatim sentence from the \
transcript","speaker":"diarization label of the quote","agent_takeable":true|false,\
"agent_work":"one line: a draft/research/internal artefact an agent could produce; never sending \
anything as Shaw","recurring":true|false}}]}}
If there are none, return {{"shaw_label":"...","shaw_label_evidence":"...","items":[]}}.
"""

_EXISTING_HEADER = """
ALREADY-KNOWN OCCURRENCES from earlier extractions of THIS call. If an item you return \
restates one of these, add "same_as_occ":"<its occ>" to that item; otherwise omit same_as_occ.
"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def build_prompt(transcript, existing=None):
    """Prompt text + transcript (capped at MAX_TRANSCRIPT_CHARS, head kept). Returns (prompt, truncated)."""
    truncated = len(transcript) > MAX_TRANSCRIPT_CHARS
    body = transcript[:MAX_TRANSCRIPT_CHARS]
    prompt = PROMPT
    if existing:
        lines = [json.dumps({"occ": e.get("occ"), "owner": e.get("owner"), "action": e.get("action"),
                             "quote": e.get("quote")}, ensure_ascii=False) for e in existing]
        prompt += _EXISTING_HEADER + "\n".join(lines) + "\n"
    return prompt + "\nTRANSCRIPT:\n" + body, truncated


def _first_object(text):
    """The first balanced top-level {...} in text (string- and escape-aware), or None."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None  # unbalanced: the output was cut off


def parse_output(stdout):
    """Model stdout -> dict, or None if no JSON object can be read.

    A ```json fenced block wins over the surrounding text (prose around it may contain stray
    braces); otherwise the first balanced object anywhere in the output is used."""
    text = stdout or ""
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1)
    candidate = _first_object(text)
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _valid_items(items):
    """Keep dict items that have a quote and a known owner; leave the survivors unchanged."""
    return [it for it in items
            if isinstance(it, dict) and it.get("quote") and it.get("owner") in _OWNERS]


def run(call, *, runner=None, existing=None, claude_bin=None, model=None, timeout=180):
    """Run the extractor over one call. Never raises for CLI or output problems; returns ok=False."""
    runner = runner or subprocess.run
    prompt, truncated = build_prompt(call.transcript, existing)
    argv = _PREFIX + [claude_bin or os.environ.get("POSTCALL_CLAUDE_BIN", "claude"), "-p"]
    model = model or os.environ.get("POSTCALL_EXTRACT_MODEL")
    if model:
        argv += ["--model", model]
    argv.append(prompt)

    try:
        # stdin=DEVNULL is deliberate (the miner's lesson: avoids the CLI answering in-character).
        proc = runner(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Extraction(ok=False, raw=None, error="timeout")
    except OSError as e:
        return Extraction(ok=False, raw=None, error=f"oserror: {e}")

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-200:]
        return Extraction(ok=False, raw=None, error=f"claude exited {proc.returncode}: {tail}")

    parsed = parse_output(proc.stdout)
    if parsed is None:
        return Extraction(ok=False, raw=None, error="bad json")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        return Extraction(ok=False, raw=None, error="bad shape: no items list")

    parsed["items"] = _valid_items(parsed["items"])
    if truncated:
        parsed["_truncated"] = True
    return Extraction(ok=True, raw=parsed)


def default_extractor(call, existing=None):
    """Pipeline hook: the production extractor with its defaults."""
    return run(call, existing=existing)
