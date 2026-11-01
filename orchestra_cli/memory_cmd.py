"""`orchestra memory`: the conversation index (scripts/episodic_memory.py).

  orchestra memory search "<words>" [--agent A] [--session S] [--generation N] [--limit N] [--json]
  orchestra memory ingest <agent> --session <id> [--generation N] [--path FILE] [--json]
  orchestra memory compaction-status [--agent A] [--limit N] [--json]

`search` is how an agent looks something up instead of relying on recall: every row says which agent, session and
generation it came from, and the transcript line (uuid) or tool call (tool id) to cite. `ingest` indexes a session's
transcript (forward from where the last ingest stopped); its file is found from the session id unless `--path`
names it, and its generation comes from the identity store unless `--generation` gives it. `compaction-status` says
what happened to each attempt to compact a seat's chat, newest first, one plain line each (when, how it was started,
what happened, why); `search` returns the same rows first when the question is about compacting.
"""
from __future__ import annotations

import argparse
import json
import sys


def add_parser(sub) -> None:
    m = sub.add_parser("memory", help="the conversation index: search what agents said and did, or index a session")
    msub = m.add_subparsers(dest="memory_cmd", required=True)
    s = msub.add_parser("search", help="rows matching every word, best first, with their agent/session/generation and ids")
    s.add_argument("q")
    s.add_argument("--agent")
    s.add_argument("--session")
    s.add_argument("--generation", type=int)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    i = msub.add_parser("ingest", help="index a session's transcript (forward from the last ingest; idempotent)")
    i.add_argument("agent")
    i.add_argument("--session", required=True)
    i.add_argument("--generation", type=int, help="default: the identity store's generation for this session")
    i.add_argument("--path", help="the transcript file (default: found from the session id)")
    i.add_argument("--json", action="store_true")
    c = msub.add_parser("compaction-status", help="each attempt to compact a chat: when, how it was started, what happened, why")
    c.add_argument("--agent")
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--json", action="store_true")


def cmd_memory(ns) -> int:
    from scripts import episodic_memory as EM
    if ns.memory_cmd == "compaction-status":
        rows = EM.attempts(ns.agent, limit=ns.limit)
        if ns.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print("no attempts to compact" + (f" for {ns.agent}" if ns.agent else "") + " yet")
        else:
            for a in rows:
                print(EM.attempt_line(a, with_agent=not ns.agent))
        return 0
    if ns.memory_cmd == "search":
        rows = EM.query(ns.q, agent=ns.agent, session=ns.session, generation=ns.generation, limit=ns.limit)
        if ns.json:
            print(json.dumps(rows, indent=2))
            return 0
        if not rows:
            print("no matches")
            return 0
        for r in rows:
            if r["kind"] == "compaction_attempt":
                print(f"[compacting] {r['agent']} · {r['snippet']}")
                continue
            where = f"{r['agent']} · session {r['session_id']}" + (f" · gen {r['generation']}" if r["generation"] is not None else "")
            cite = f"tool {r['tool_id']}" if r["tool_id"] else f"msg {r['uuid']}"
            print(f"[{r['kind']}] {where} · {cite} · {r['ts'] or ''}\n    {r['snippet']}")
        return 0
    path = ns.path or EM.find_transcript(ns.session)
    if not path:
        print(f"memory ingest: no transcript found for session {ns.session}; name it with --path", file=sys.stderr)
        return 2
    gen = ns.generation if ns.generation is not None else EM.generation_of(ns.session)
    try:
        rep = EM.ingest_session(ns.agent, ns.session, gen, path)
    except EM.UnsupportedTranscript as e:
        print(f"memory ingest: refused: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"memory ingest: no such file: {path}", file=sys.stderr)
        return 2
    rep = {**rep, "agent": ns.agent, "session": ns.session, "generation": gen}
    print(json.dumps(rep) if ns.json else
          f"indexed {rep['added']} new rows for {ns.agent} (session {ns.session}, generation {gen if gen is not None else 'unknown'})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="orchestra memory")
    sub = p.add_subparsers(dest="command", required=True)
    add_parser(sub)
    return cmd_memory(p.parse_args(["memory", *(argv or [])]))
