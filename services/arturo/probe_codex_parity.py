"""By-effect proof for the codex parity fix (2026-09-19) — NOT part of the pytest suite.

Shells the REAL codex CLI (no mocks) through services.arturo.brain.RuntimeBrain exactly as
Arturo's dispatcher would, and prints, per run: latency, tool_calls count, and whether the
result honours the protocol. Run manually against an authed codex install:

    python3 services/arturo/probe_codex_parity.py

Before today's fix this probe measured codex at 0/3 (never emitted the envelope, and twice
fabricated a successful action with no tool call in the transcript). The bar is 3/3 clean,
plus a negative control proving the schema does not simply force tool_calls on every turn.

CODEX_HOME note: this repo's own interactive fleet config (~/.codex/hooks.json) intercepts
spawn-shaped turns with its own tool machinery and is NOT representative of the clean CLI
install Arturo's service account runs against. Point CODEX_HOME at a directory holding only
auth.json (+ a minimal config.toml) to reproduce production conditions; a contaminated
CODEX_HOME will UNDER-report codex's real reliability, not over-report it.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.arturo import brain as b  # noqa: E402

TOOLS = [{"function": {"name": "spawn_agent",
          "description": "Create and launch a registered agent seat.",
          "parameters": {"type": "object", "properties": {"session": {"type": "string"}, "task": {"type": "string"}},
                         "required": ["session"]}}}]
SPAWN_ASK = "Commission an agent named probe-seat to say hello and park. Use the tool."
NEGATIVE_ASK = "What is 2+2?"


def _run(ask: str, tool_choice: str, tools) -> tuple[float, "object"]:
    br = b.RuntimeBrain("codex", "codex")
    sys_prompt = "You are Arturo.\n" + b.tool_protocol_block(tools, tool_choice)
    t0 = time.time()
    resp = br.complete([{"role": "system", "content": sys_prompt}, {"role": "user", "content": ask}],
                       tools, tool_choice)
    return time.time() - t0, resp


def main() -> int:
    ok = True
    print("== positive: spawn_agent, tool_choice=required, 3 runs ==")
    for i in range(1, 4):
        dt, resp = _run(SPAWN_ASK, "required", TOOLS)
        calls = resp.choices[0].message.tool_calls or []
        good = len(calls) == 1 and calls[0].function.name == "spawn_agent"
        ok &= good
        detail = f"-> {calls[0].function.name} {calls[0].function.arguments}" if calls \
            else f"NO TOOL CALL: {resp.choices[0].message.content!r}"
        print(f"  run {i}: {dt:5.1f}s  {'PASS' if good else 'FAIL'}  {detail}")

    print("== negative control: plain question must stay prose, no tool call ==")
    dt, resp = _run(NEGATIVE_ASK, "auto", TOOLS)
    calls = resp.choices[0].message.tool_calls
    text = resp.choices[0].message.content or ""
    neg_good = not calls and "4" in text
    ok &= neg_good
    print(f"  {dt:5.1f}s  {'PASS' if neg_good else 'FAIL'}  tool_calls={calls}  text={text!r}")

    print(f"\n{'3/3 + negative control: PARITY REACHED' if ok else 'NOT AT PARITY'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
