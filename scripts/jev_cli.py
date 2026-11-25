#!/usr/bin/env python3
"""Jev tools for any seat (Claude, Gemini, Codex): a CLI, and the same tools as a stdio MCP server.

  jev_cli.py ask   --questions JSON [--state S] [--paths P ...] [--command C] [--cwd D]
  jev_cli.py file  PATH --question Q [--kind bool|choice|score] [--options JSON] [--levels JSON] [--yes Y --no N]
  jev_cli.py files PATTERN ... --questions JSON [--recursive] [--concurrency N]
  jev_cli.py pick  --question Q PATH ...
  jev_cli.py should-compact (--transcript PATH | --session-id SID)
  jev_cli.py verdict --session-id SID           last persisted Level 7 verdict, no call
  jev_cli.py mcp                                 serve ask_jev / ask_jev_file / ask_jev_files /
                                                 should_i_compact over MCP stdio (JSON-RPC 2.0)

Every command prints one JSON object. The provider follows JEV_BACKEND / the keys
(see jev.client); JEV_BACKEND=mock runs fully offline.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jev import compact, files  # noqa: E402
from jev.ask_jev import ASK_JEV_DESCRIPTION, AskStateError, ask_jev_tool  # noqa: E402
from jev.client import JevError, default_decide  # noqa: E402
from jev.types import QuestionValidationError  # noqa: E402

SERVER_INFO = {"name": "jev", "version": "1.0.0"}
PROTOCOL_VERSION = "2025-06-18"


def find_transcript(session_id: str) -> str:
    root = os.path.expanduser("~/.claude/projects")
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    hits = glob.glob(os.path.join(root, "*", f"{safe}.jsonl")) if safe else []
    if not hits:
        raise FileNotFoundError(f"no transcript for session {session_id!r} under {root}")
    return max(hits, key=os.path.getmtime)


# ---------------------------------------------------------------- tool implementations

def t_ask_jev(args: dict, decide=default_decide) -> dict:
    return ask_jev_tool(state=args.get("state"), paths=args.get("paths"), command=args.get("command"),
                        questions_json=args.get("questions_json"), cwd=args.get("cwd") or os.getcwd(),
                        decide=decide)


def t_ask_jev_file(args: dict, decide=default_decide) -> dict:
    return files.ask_jev_file(args["path"], args["question"], args.get("cwd") or os.getcwd(),
                              kind=args.get("kind", "bool"), options=args.get("options"),
                              levels=args.get("levels"), yes=args.get("yes"), no=args.get("no"), decide=decide)


def t_ask_jev_files(args: dict, decide=default_decide) -> dict:
    return files.ask_files(args["patterns"], args["questions_json"], args.get("cwd") or os.getcwd(),
                           recursive=bool(args.get("recursive")),
                           concurrency=int(args.get("concurrency") or files.DEFAULT_CONCURRENCY), decide=decide)


def t_should_i_compact(args: dict, decide=default_decide) -> dict:
    if args.get("transcript_path"):
        path = args["transcript_path"]
    elif args.get("session_id"):
        path = find_transcript(args["session_id"])
    else:
        raise ValueError("pass session_id or transcript_path")
    return compact.should_i_compact(path, decide)


QUESTIONS_SCHEMA = {"description": "Jev question block: a JSON object (or JSON string) keyed by question id.",
                    "type": ["object", "string"]}

TOOLS = [
    {"name": "ask_jev", "description": ASK_JEV_DESCRIPTION, "fn": t_ask_jev,
     "inputSchema": {"type": "object", "required": ["questions_json"], "properties": {
         "questions_json": QUESTIONS_SCHEMA,
         "state": {"type": ["string", "object"], "description": "What only you can say. Not file contents."},
         "paths": {"type": "array", "items": {"type": "string"}, "description": "Files/globs code reads into files[path] (max 20)."},
         "command": {"type": "string", "description": "A read-only command code runs; result lands in `output`."},
         "cwd": {"type": "string"}}}},
    {"name": "ask_jev_file",
     "description": ("Ask Jev one question about one file without reading it into your context. kind=bool -> "
                     "{answer, noul}; kind=choice with options {name: description} -> {choice, confidence, probabilities} "
                     "(an `other` exit is added); kind=score with levels [low..high] -> {score, nearest, confidence}. "
                     "Write the question against `content`. Not for exact lookups: use grep."),
     "fn": t_ask_jev_file,
     "inputSchema": {"type": "object", "required": ["path", "question"], "properties": {
         "path": {"type": "string"}, "question": {"type": "string"},
         "kind": {"type": "string", "enum": ["bool", "choice", "score"]},
         "options": {"type": "object"}, "levels": {"type": "array", "items": {"type": "string"}},
         "yes": {"type": "string"}, "no": {"type": "string"}, "cwd": {"type": "string"}}}},
    {"name": "ask_jev_files",
     "description": ("Ask the same Jev questions about many files (globs, dirs, paths; up to 255), one call per file "
                     "in parallel. Returns answers per path plus what was skipped and why. No file enters your context. "
                     "Use it to scout a codebase: which of these files handle auth, which look risky, which are tests."),
     "fn": t_ask_jev_files,
     "inputSchema": {"type": "object", "required": ["patterns", "questions_json"], "properties": {
         "patterns": {"type": "array", "items": {"type": "string"}}, "questions_json": QUESTIONS_SCHEMA,
         "recursive": {"type": "boolean"}, "concurrency": {"type": "integer"}, "cwd": {"type": "string"}}}},
    {"name": "should_i_compact",
     "description": ("Ask whether now is a good moment to rotate (blue-green) this session. Returns should_compact, a tier "
                     "(silent, notice, recommend, request), the rotation action, the reason and the context percent. "
                     "Pass your session_id or transcript_path. Cheap; call it freely at a clean point."),
     "fn": t_should_i_compact,
     "inputSchema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "transcript_path": {"type": "string"}}}},
]
TOOL_INDEX = {t["name"]: t for t in TOOLS}
# What a tool call may fail with that the agent should read, not the server crash on.
# ValueError covers QuestionValidationError / AskStateError / bad JSON / bad kind: the agent's input.
TOOL_ERRORS = (JevError, QuestionValidationError, AskStateError, files.FileStateError, ValueError,
               FileNotFoundError)


def call_tool(name: str, args: dict, decide=default_decide) -> dict:
    tool = TOOL_INDEX.get(name)
    if not tool:
        return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
    args = {k: v for k, v in (args or {}).items() if v is not None}
    missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
    if missing:
        return {"content": [{"type": "text", "text": f"{name}: missing argument(s) {', '.join(missing)}"}],
                "isError": True}
    try:
        result = tool["fn"](args, decide)
    except TOOL_ERRORS as err:
        return {"content": [{"type": "text", "text": f"{name}: {err}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(result, indent=1, ensure_ascii=False)}],
            "structuredContent": result, "isError": False}


def handle(msg: dict, decide=default_decide):
    """One JSON-RPC message -> one response dict, or None for notifications."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION,
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        result = call_tool(params.get("name"), params.get("arguments") or {}, decide)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve_mcp(stdin=sys.stdin, stdout=sys.stdout, decide=default_decide) -> int:
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        reply = handle(msg, decide)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


# ---------------------------------------------------------------- CLI

def _json_arg(s):
    return json.loads(s) if s is not None else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jev_cli.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("--questions", required=True)
    a.add_argument("--state")
    a.add_argument("--paths", nargs="*")
    a.add_argument("--command")
    a.add_argument("--cwd")
    f = sub.add_parser("file")
    f.add_argument("path")
    f.add_argument("--question", required=True)
    f.add_argument("--kind", default="bool", choices=["bool", "choice", "score"])
    f.add_argument("--options")
    f.add_argument("--levels")
    f.add_argument("--yes")
    f.add_argument("--no")
    f.add_argument("--cwd")
    fs = sub.add_parser("files")
    fs.add_argument("patterns", nargs="+")
    fs.add_argument("--questions", required=True)
    fs.add_argument("--recursive", action="store_true")
    fs.add_argument("--concurrency", type=int)
    fs.add_argument("--cwd")
    p = sub.add_parser("pick")
    p.add_argument("--question", required=True)
    p.add_argument("paths", nargs="+")
    sc = sub.add_parser("should-compact")
    sc.add_argument("--transcript")
    sc.add_argument("--session-id")
    v = sub.add_parser("verdict")
    v.add_argument("--session-id", required=True)
    sub.add_parser("mcp")
    ns = ap.parse_args(argv)

    if ns.cmd == "mcp":
        return serve_mcp()
    if ns.cmd == "verdict":
        print(json.dumps(compact.latest_verdict(ns.session_id), indent=1))
        return 0
    if ns.cmd == "pick":
        out = files.pick_first_file(ns.question, [{"path": x} for x in ns.paths])
        print(json.dumps(out, indent=1))
        return 0
    if ns.cmd == "ask":
        name, args = "ask_jev", {"questions_json": ns.questions, "state": ns.state, "paths": ns.paths,
                                 "command": ns.command, "cwd": ns.cwd}
    elif ns.cmd == "file":
        name, args = "ask_jev_file", {"path": ns.path, "question": ns.question, "kind": ns.kind,
                                      "options": _json_arg(ns.options), "levels": _json_arg(ns.levels),
                                      "yes": ns.yes, "no": ns.no, "cwd": ns.cwd}
    elif ns.cmd == "files":
        name, args = "ask_jev_files", {"patterns": ns.patterns, "questions_json": ns.questions,
                                       "recursive": ns.recursive, "concurrency": ns.concurrency, "cwd": ns.cwd}
    else:
        name, args = "should_i_compact", {"transcript_path": ns.transcript, "session_id": ns.session_id}
    out = call_tool(name, args)
    if out["isError"]:
        print(json.dumps({"error": out["content"][0]["text"]}), file=sys.stderr)
        return 1
    print(json.dumps(out["structuredContent"], indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
