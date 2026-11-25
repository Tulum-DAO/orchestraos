"""Level 10: agentic Jev. One ask_jev tool over state, paths, and a command.

Port of ten-levels-of-jev src/levels/level10. The agent passes its own state, paths for
code to read, a command for code to run, and its own question block. Code assembles ONE
state, validates, and makes ONE call. The agent never receives the files or the output,
only the answers.

Fleet difference from the reference: `command` runs outside Claude Code's Bash tool, so
it would bypass every PreToolUse guard (delete-guard, git_state_guard, jev bash gate).
It therefore runs only when the Level 6 bash gate allows it AND Jev calls it read_only.
If Jev cannot judge it, the command does not run (fail closed): the agent still has its
own Bash tool, under its own guards, for anything else.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Dict, List, Optional

from .client import JevError, default_decide
from .files import FileStateError, expand_patterns, parse_questions, prune_files, read_file_state
from .guard import BASH_QUESTIONS, gate_bash
from .types import LIMITS, Questions, State

Decide = Callable[[State, Questions], Dict[str, Any]]


def tokens_of(text: str) -> int:
    """Roughly four characters per token, the same estimate the file reader uses."""
    return -(-len(text) // 4)


STATE_TOKEN_BUDGET = LIMITS.TOTAL_TOKEN_BUDGET - 4000
MAX_FILES_PER_CALL = 20
MAX_OWN_STATE_CHARS = 8000
COMMAND_TIMEOUT_S = 120
# read_only must be the top pick and at least an even bet. Measured live 2026-10-06: `pytest -q tests`
# came back read_only 0.59, so a 0.6 floor refused the tool's headline use (classify a test failure).
READ_ONLY_FLOOR = 0.5

ASK_JEV_DESCRIPTION = "\n".join([
    "Ask Jev, a fast decision model, typed questions about one situation: files, a command's output, your own state, or any mix. It answers in about 300 ms for a fraction of a cent, and each answer is a number you can branch on, not prose. Use it whenever a judgment call would otherwise cost you a long think.",
    "",
    "Do not paste content you already have; that costs output tokens. Pass paths and code reads the files into files[\"path\"]. Pass command and code runs it in the repo and puts the result into output {command, exit_code, stdout, stderr}; the command must be read-only (tests, git diff, grep, ls) or it is refused. Use state for what only you can say: a customer report, your plan, a line of context. Plain text or a JSON object as a string; your field names are kept as is. You can combine all three, and you never receive the files or the output, only the answers.",
    "",
    "One call judges one situation: up to 20 files and about 60k tokens in total. Over that the call is refused with a message naming the parts and a split that fits; make two calls with the same questions_json. For many files judged separately use ask_jev_files instead.",
    "",
    "questions_json: a JSON object keyed by question id. Three types:",
    '  noul   {"type":"noul","instructions":"Is `output` a real failure rather than a flaky one?","criteria":{"true":"...","false":"..."}}  -> { noul: 0..1 }',
    '  choice {"type":"choice","instructions":"What kind of failure is `output`?","criteria":{"bug_in_code":"...","wrong_test":"...","environment":"...","other":"..."}}  -> { choice, confidence, probabilities }',
    '  score  {"type":"score","instructions":"How risky is `diff`?","criteria":["Isolated, tested","Some callers","Security sensitive, no tests"]}  -> { score, confidence, legend }',
    "",
    "Write questions against files[\"path\"], output, or your own field names. Good uses: run the tests through command and classify the failure before choosing a fix, put git diff through command and score its risk before committing, pass the customer's words as state with the relevant paths and decide bug or expected, decide whether a request is clear enough to plan.",
    "Ask every question you might need in one call; they share the state. Always give a choice an `other` option. Describe situations, not degrees.",
    "Not for: exact lookups, counting, math, or anything a grep answers. Not a substitute for reading code you need to edit.",
])


class AskStateError(ValueError):
    """The situation cannot be assembled into one call; the message says how to split it."""


def parse_state(raw: Any) -> State:
    """A JSON string becomes structured state; anything else is sent as the string it is."""
    if not isinstance(raw, str):
        return raw
    t = raw.strip()
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        try:
            return json.loads(t)
        except ValueError:
            pass
    return raw


def ask_jev(raw_state: Any, questions_json: Any, decide: Decide = default_decide) -> Dict[str, Any]:
    """Option A: any state, any questions, one call. Empty state is refused."""
    state = parse_state(raw_state)
    if (isinstance(state, str) and not state.strip()) or (not isinstance(state, str) and not state):
        raise AskStateError("state is empty")
    questions = parse_questions(questions_json)
    result = decide(state, questions)
    return {"answers": result["answers"], "usage": result.get("usage"), "model": result.get("model")}


def _fmt_k(t: int) -> str:
    return (f"{t / 1000:.1f}k" if t >= 1000 else str(t)) + " tokens"


def suggest_split(parts: List[Dict[str, Any]], budget: int) -> List[List[Dict[str, Any]]]:
    """Greedy first fit, largest first: groups whose token sums fit the budget."""
    bins: List[Dict[str, Any]] = []
    for part in sorted(parts, key=lambda p: -p["tokens"]):
        target = next((b for b in bins if b["total"] + part["tokens"] <= budget), None)
        if target:
            target["items"].append(part)
            target["total"] += part["tokens"]
        else:
            bins.append({"total": part["tokens"], "items": [part]})
    return [b["items"] for b in bins]


def _describe(parts: List[Dict[str, Any]]) -> str:
    files = [p["name"] for p in parts if p["kind"] == "file"]
    bits = []
    if any(p["kind"] == "own" for p in parts):
        bits.append("your state")
    if files:
        bits.append(f"paths [{', '.join(files)}]")
    if any(p["kind"] == "output" for p in parts):
        bits.append("the command")
    return f"{' + '.join(bits)} ({_fmt_k(sum(p['tokens'] for p in parts))})"


def overflow_message(parts: List[Dict[str, Any]], budget: int) -> str:
    total = sum(p["tokens"] for p in parts)
    groups = suggest_split(parts, budget)
    oversize = [p for p in parts if p["tokens"] > budget]
    ordered = sorted(parts, key=lambda p: -p["tokens"])
    lines = [
        f"ask_jev: the state is {_fmt_k(total)}, the limit per call is {_fmt_k(budget)}.",
        f"Parts: {', '.join(p['name'] + ' ' + _fmt_k(p['tokens']) for p in ordered)}.",
    ]
    if oversize:
        lines.append(f"Too large for any single call: {', '.join(p['name'] for p in oversize)}. "
                     "Narrow it (a smaller file, a command with less output) or leave it out.")
    if len(groups) > 1 and not oversize:
        lines.append(f"Split into {len(groups)} calls with the same questions_json: "
                     + "; ".join(f"call {i + 1}: {_describe(g)}" for i, g in enumerate(groups)) + ".")
    return " ".join(lines)


def run_command(command: str, cwd: str) -> Dict[str, Any]:
    try:
        p = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True, text=True,
                           timeout=COMMAND_TIMEOUT_S, errors="replace")
        return {"command": command, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except subprocess.TimeoutExpired as err:
        out = err.stdout.decode(errors="replace") if isinstance(err.stdout, bytes) else (err.stdout or "")
        return {"command": command, "exit_code": None, "stdout": out,
                "stderr": f"timed out after {COMMAND_TIMEOUT_S}s"}


def command_gate(command: str, cwd: str, decide: Decide = default_decide) -> Dict[str, Any]:
    """The bash gate plus a read-only requirement. Fails closed: no judgment, no run."""
    try:
        answers = decide({"command": command, "cwd": cwd}, BASH_QUESTIONS)["answers"]
    except JevError as err:
        return {"allow": False, "reason": f"could not judge the command ({type(err).__name__}); not run"}
    d = gate_bash(answers)
    if d["block"]:
        return {"allow": False, "reason": f"blocked: {d['reason']}"}
    effect = answers["effect"]
    if effect["choice"] != "read_only" or effect["confidence"] < READ_ONLY_FLOOR:
        return {"allow": False, "reason": (f"not read-only ({effect['choice']} {effect['confidence']:.2f}); "
                                           "ask_jev only runs commands that change nothing. Use your Bash tool.")}
    return {"allow": True, "reason": d["reason"]}


def assemble_state(state: Any = None, paths: Optional[List[str]] = None, command: Optional[str] = None,
                   cwd: str = ".", run: Callable[[str, str], Dict[str, Any]] = run_command,
                   gate: Optional[Callable[[str, str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Own state first, then files, then the command's output. Nothing is truncated: over
    the file cap or the token budget the call is refused with a message that says how to split."""
    own = {} if state is None or state == "" else parse_state(state)
    base: Dict[str, Any] = {"text": own} if isinstance(own, str) else {"items": own} if isinstance(own, list) else dict(own)
    own_text = json.dumps(base, ensure_ascii=False, separators=(",", ":"))
    if len(own_text) > MAX_OWN_STATE_CHARS:
        raise AskStateError(
            f"ask_jev: your state is {_fmt_k(tokens_of(own_text))}; the limit for your own state is "
            f"{_fmt_k(tokens_of('x' * MAX_OWN_STATE_CHARS))}. Do not paste file contents or command output; "
            "pass paths or command instead and code fetches them.")
    parts: List[Dict[str, Any]] = []
    if base:
        parts.append({"name": "your state", "tokens": tokens_of(own_text), "kind": "own"})

    skipped: List[Dict[str, str]] = []
    files: Dict[str, str] = {}
    if paths:
        pruned = prune_files(expand_patterns(paths, cwd, True), cwd, MAX_FILES_PER_CALL + 1)
        skipped.extend(pruned["skipped"])
        if len(pruned["files"]) > MAX_FILES_PER_CALL:
            raise AskStateError(
                f"ask_jev: paths expanded to more than {MAX_FILES_PER_CALL} files. This tool judges one situation "
                "in one call. For many files use ask_jev_files, one call per file in parallel, or narrow the paths.")
        for path in pruned["files"]:
            try:
                files[path] = read_file_state(path, cwd)["content"]
                parts.append({"name": path, "tokens": tokens_of(files[path]), "kind": "file"})
            except FileStateError as err:
                skipped.append({"path": path, "reason": str(err)})

    output = None
    command_refused = None
    if command and command.strip():
        cmd = command.strip()
        verdict = gate(cmd, cwd) if gate else {"allow": True, "reason": "no gate"}
        if not verdict["allow"]:
            command_refused = verdict["reason"]
        else:
            output = run(cmd, cwd)
            parts.append({"name": f"output of `{output['command']}`",
                          "tokens": tokens_of(output["stdout"] + output["stderr"]), "kind": "output"})

    if not parts:
        if command_refused:
            raise AskStateError(f"ask_jev: the command was refused ({command_refused}) and nothing else was given.")
        raise AskStateError("ask_jev: nothing to judge. Pass state, paths, or command.")

    total = sum(p["tokens"] for p in parts)
    if total > STATE_TOKEN_BUDGET:
        raise AskStateError(overflow_message(parts, STATE_TOKEN_BUDGET))

    assembled: Dict[str, Any] = dict(base)
    if files:
        assembled["files"] = files
    if output:
        assembled["output"] = output
    return {
        "state": assembled,
        "summary": {
            "own_fields": list(base.keys()),
            "files": list(files.keys()),
            "output": (f"{output['command']}, exit {output['exit_code']}, "
                       f"{_fmt_k(tokens_of(output['stdout'] + output['stderr']))}") if output else None,
            "command_refused": command_refused,
            "skipped": skipped,
            "tokens": total,
        },
    }


def ask_jev_tool(state: Any = None, paths: Optional[List[str]] = None, command: Optional[str] = None,
                 questions_json: Any = None, cwd: str = ".", decide: Decide = default_decide,
                 run: Callable[[str, str], Dict[str, Any]] = run_command) -> Dict[str, Any]:
    """The full tool: validate questions first (before any read or run), assemble, one call."""
    questions = parse_questions(questions_json)
    asm = assemble_state(state, paths, command, cwd, run, gate=lambda c, d: command_gate(c, d, decide))
    result = decide(asm["state"], questions)
    return {"answers": result["answers"], "usage": result.get("usage"), "model": result.get("model"),
            "state_summary": asm["summary"]}


# ---------------------------------------------------------------- spend ledger (option C)

# Jev's list price per input token when the endpoint reports no cost. Output is free.
JEV_INPUT_USD_PER_M = 0.042


def empty_ledger() -> Dict[str, float]:
    return {"calls": 0, "questions": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}


def record(ledger: Dict[str, float], usage: Optional[Dict[str, Any]], question_count: int) -> Dict[str, float]:
    usage = usage or {}
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    cost = usage.get("cost")
    return {
        "calls": ledger["calls"] + 1,
        "questions": ledger["questions"] + question_count,
        "input_tokens": ledger["input_tokens"] + inp,
        "output_tokens": ledger["output_tokens"] + out,
        "usd": ledger["usd"] + (cost if isinstance(cost, (int, float)) else inp * JEV_INPUT_USD_PER_M / 1e6),
    }


def summarize(ledger: Dict[str, float], agent_usd: float) -> str:
    if ledger["calls"] == 0:
        return "No Jev calls this session."
    ratio = round(agent_usd / ledger["usd"]) if ledger["usd"] > 0 else 0
    calls, qs = int(ledger["calls"]), int(ledger["questions"])
    return (f"{calls} Jev call{'' if calls == 1 else 's'}, {qs} question{'' if qs == 1 else 's'}, ${ledger['usd']:.6f}"
            + (f", the agent's own spend was {ratio}x that" if ratio else ""))
