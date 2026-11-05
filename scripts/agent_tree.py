#!/usr/bin/env python3
"""Read-only tree of a seat's Claude subagents at every depth: seat -> subagent -> helper.

    python3 scripts/agent_tree.py <seat|session-id|transcript.jsonl|all> [--json] [--watch [S]]
                                  [--recent S] [--all-finished]

Source = the files Claude Code (2.1.x) writes, nothing else:
    ~/.claude/projects/<slug>/<sid>.jsonl                       the seat (depth 0)
    ~/.claude/projects/<slug>/<sid>/subagents/agent-<id>.jsonl  one per subagent, every depth
    ~/.claude/projects/<slug>/<sid>/subagents/agent-<id>.meta.json
        {agentType, description, toolUseId, parentAgentId (depth >= 2 only), spawnDepth}

JSON node (the API contract; the dashboard and the phone render it):
    {agent_id, parent_agent_id, depth, type, label, state, started_at, last_activity_at,
     current_tool, last_tool, tokens_own, tokens_rollup, tokens_cache_read, ctx_tokens,
     elapsed_s, transcript_path, children[]}
state:
    running   not finished (subagent_files.subagent_finished) and written in the last 15 min
    waiting   own file not running, but a descendant is running/waiting: a parent blocked inside
              its Agent call (its file does not move while the helper works), or one whose turn
              ended over background children (it resumes when they report)
    finished  last conversational record is its final answer (end_turn / stop_sequence)
    stale     not finished, no write in 15 min, nothing below it alive
tokens_own = fresh tokens this node's model calls cost: input + cache_creation + output, one
    count per API message id (Claude writes one record per content block, repeating usage).
    Cache reads are reported apart (tokens_cache_read) because they dwarf the rest 100:1.
tokens_rollup = tokens_own + every descendant's tokens_rollup.
ctx_tokens = the node's context size at its last call (input + cache_creation + cache_read).
current_tool = the tool of a tool_use still waiting for its result, else null; last_tool = the
    last tool_use the node made.
Times are UTC ISO-8601 with a Z (machine data); elapsed_s is seconds.
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from subagent_files import SUBAGENT_STALE_S, subagent_finished  # noqa: E402

# The registry is DATA: $ORCHESTRA_DIR/state/orchestra-registry.db (default ~/.orchestra), never the checkout.
ORCH_DIR = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra")
CLAUDE_PROJECTS = os.environ.get("CLAUDE_PROJECTS_DIR", os.path.expanduser("~/.claude/projects"))
RECENT_S = 3600      # finished/stale nodes older than this are hidden unless --all-finished


def _iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


class TranscriptScan:
    """Incremental reader of one transcript: re-reads only bytes appended since the last scan,
    so --watch and the API stream cost O(new lines). A shrunk file (rewrite) starts over."""

    def __init__(self, path):
        self.path = path
        self._reset()

    def _reset(self):
        self.offset = 0
        self.started_at = None
        self.usage = {}            # API message id -> usage dict (last record for that id wins)
        self.last_tool = None
        self.pending = {}          # tool_use id -> tool name, until its tool_result arrives
        self.last_usage = None

    def update(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.offset:
            self._reset()
        if size == self.offset:
            return
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        cut = data.rfind(b"\n")
        if cut < 0:
            return                 # no complete line yet
        self.offset += cut + 1
        for line in data[:cut].split(b"\n"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                self._take(rec)

    def _take(self, rec):
        if self.started_at is None and rec.get("timestamp"):
            self.started_at = _parse_ts(rec["timestamp"])
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        if rec.get("type") == "assistant":
            u = msg.get("usage")
            if isinstance(u, dict):
                self.usage[msg.get("id") or rec.get("uuid")] = u
                self.last_usage = u
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    self.last_tool = b.get("name")
                    self.pending[b.get("id")] = b.get("name")
        elif rec.get("type") == "user":
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.pending.pop(b.get("tool_use_id"), None)

    def tokens(self):
        own = cache = 0
        for u in self.usage.values():
            own += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) \
                + int(u.get("output_tokens") or 0)
            cache += int(u.get("cache_read_input_tokens") or 0)
        return own, cache

    def ctx_tokens(self):
        u = self.last_usage or {}
        return int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) \
            + int(u.get("cache_read_input_tokens") or 0)

    def current_tool(self):
        return next(reversed(self.pending.values()), None) if self.pending else None


_SCANS = {}


def _scan(path):
    s = _SCANS.get(path)
    if s is None:
        s = _SCANS[path] = TranscriptScan(path)
    s.update()
    return s


def _read_meta(path):
    try:
        with open(path) as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _node(agent_id, parent, depth, kind, label, path, now, finished_fn):
    sc = _scan(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    own, cache = sc.tokens()
    if finished_fn(path):
        state = "finished"
    elif mtime is not None and now - mtime <= SUBAGENT_STALE_S:
        state = "running"
    else:
        state = "stale"
    return {"agent_id": agent_id, "parent_agent_id": parent, "depth": depth, "type": kind,
            "label": label, "state": state, "started_at": sc.started_at, "last_activity_at": mtime,
            "current_tool": sc.current_tool() if state != "finished" else None,
            "last_tool": sc.last_tool, "tokens_own": own, "tokens_rollup": own,
            "tokens_cache_read": cache, "ctx_tokens": sc.ctx_tokens(), "elapsed_s": None,
            "transcript_path": path, "children": []}


def _finish(node, now):
    """Post-order: roll up tokens, promote a quiet parent with live descendants to waiting."""
    live_below = False
    for c in node["children"]:
        _finish(c, now)
        node["tokens_rollup"] += c["tokens_rollup"]
        live_below = live_below or c["state"] in ("running", "waiting")
    if live_below and node["state"] in ("stale", "finished"):
        node["state"] = "waiting"     # a turn that ended over background children resumes on them
    start, last = node["started_at"], node["last_activity_at"]
    if start:
        end = now if node["state"] in ("running", "waiting") else (last or start)
        node["elapsed_s"] = round(max(0.0, end - start), 1)
    node["started_at"], node["last_activity_at"] = _iso(start), _iso(last)
    return node


def _prune(node, now, recent_s):
    """Keep running/waiting nodes, finished/stale ones active within recent_s, and every ancestor
    of a kept node. The seat (depth 0) is always kept."""
    kept = []
    for c in node["children"]:
        if _prune(c, now, recent_s):
            kept.append(c)
    node["children"] = kept
    if node["depth"] == 0 or kept or node["state"] in ("running", "waiting"):
        return True
    last = _parse_ts(node["last_activity_at"]) or 0
    return now - last <= recent_s


def build_tree(transcript_path, seat=None, now=None, recent_s=RECENT_S, all_finished=False,
               finished_fn=subagent_finished):
    """The tree for one Claude session transcript (<sid>.jsonl). Finished/stale nodes older than
    recent_s are dropped unless all_finished (their tokens still count in the rollup)."""
    now = time.time() if now is None else now
    sid = os.path.basename(transcript_path)[:-len(".jsonl")]
    root = _node(sid, None, 0, "seat", seat or sid, transcript_path, now, finished_fn)
    sub = os.path.join(transcript_path[:-len(".jsonl")], "subagents")
    nodes, parents = {}, {}
    for p in sorted(glob.glob(os.path.join(sub, "agent-*.jsonl"))):
        aid = os.path.basename(p)[len("agent-"):-len(".jsonl")]
        meta = _read_meta(p[:-len(".jsonl")] + ".meta.json")
        depth = meta.get("spawnDepth")
        parents[aid] = meta.get("parentAgentId")
        nodes[aid] = _node(aid, None, depth if isinstance(depth, int) else 1,
                           meta.get("agentType") or "subagent", meta.get("description") or aid,
                           p, now, finished_fn)
    for aid, n in nodes.items():
        par = parents.get(aid)
        if par and par in nodes:
            n["parent_agent_id"] = par
            nodes[par]["children"].append(n)
        else:
            # depth 1 has no parentAgentId: its parent is the seat. A named parent whose files are
            # gone also hangs off the seat, keeping its id so the gap is visible.
            n["parent_agent_id"] = par or sid
            root["children"].append(n)
    for n in [root, *nodes.values()]:
        n["children"].sort(key=lambda c: c["started_at"] or 0)
    _finish(root, now)
    if not all_finished:
        _prune(root, now, recent_s)
    return root


def _registry():
    return sqlite3.connect(f"file:{os.path.join(ORCH_DIR, 'state', 'orchestra-registry.db')}?mode=ro",
                           uri=True)


def transcript_for_sid(sid):
    hits = glob.glob(os.path.join(CLAUDE_PROJECTS, "*", f"{sid}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def seat_sessions(target):
    """[(seat, transcript_path)] for a seat root, 'all' canonical seats, a session id or a path.
    Seats whose session is not a Claude transcript on this box (codex, gemini) are skipped."""
    if target.endswith(".jsonl") and os.path.exists(target):
        return [(None, target)]
    with _registry() as db:
        if target == "all":
            rows = db.execute("SELECT c.root, g.session_id FROM canonical c JOIN generations g "
                              "ON g.id = c.generation_id ORDER BY c.root").fetchall()
        else:
            rows = db.execute("SELECT c.root, g.session_id FROM canonical c JOIN generations g "
                              "ON g.id = c.generation_id WHERE c.root = ?", (target,)).fetchall()
    if not rows and target != "all":
        rows = [(None, target)]          # not a seat: try it as a session id
    out = []
    for seat, sid in rows:
        path = transcript_for_sid(sid) if sid else None
        if path:
            out.append((seat, path))
    return out


def _fmt_tokens(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def _fmt_age(now, iso):
    t = _parse_ts(iso)
    if t is None:
        return "-"
    s = int(now - t)
    return f"{s}s" if s < 120 else f"{s // 60}m" if s < 7200 else f"{s // 3600}h"


def render(node, now, prefix="", last=True, out=None):
    out = [] if out is None else out
    tool = node["current_tool"] or (f"last {node['last_tool']}" if node["last_tool"] else "")
    head = node["label"] if node["depth"] == 0 else f"{node['type']}: {node['label']}"
    line = (f"{head}  [{node['state']}]  {tool}  wrote {_fmt_age(now, node['last_activity_at'])} ago"
            f"  tok {_fmt_tokens(node['tokens_own'])}/{_fmt_tokens(node['tokens_rollup'])}")
    if node["depth"] == 0:
        out.append(line)
    else:
        out.append(prefix + ("└─ " if last else "├─ ") + line)
        prefix += "   " if last else "│  "
    for i, c in enumerate(node["children"]):
        render(c, now, prefix, i == len(node["children"]) - 1, out)
    return out


def snapshot(target, recent_s=RECENT_S, all_finished=False, now=None):
    now = time.time() if now is None else now
    return [build_tree(p, seat, now=now, recent_s=recent_s, all_finished=all_finished)
            for seat, p in seat_sessions(target)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", help="seat root, 'all', a Claude session id, or a transcript path")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--watch", nargs="?", const=2.0, type=float, metavar="S")
    ap.add_argument("--recent", type=float, default=RECENT_S, metavar="S",
                    help="hide finished/stale nodes quiet longer than this (default 3600)")
    ap.add_argument("--all-finished", action="store_true", help="show every node ever spawned")
    a = ap.parse_args(argv)
    while True:
        now = time.time()
        trees = snapshot(a.target, a.recent, a.all_finished, now)
        if a.target == "all" and not a.all_finished:
            trees = [t for t in trees if t["children"]]     # 'all' = seats with subagents to show
        if a.json:
            print(json.dumps(trees if a.target == "all" else (trees[0] if trees else None)), flush=True)
        else:
            if a.watch:
                sys.stdout.write("\x1b[H\x1b[2J")
            for t in trees:
                print("\n".join(render(t, now)))
            if not trees:
                print(f"no Claude session found for {a.target}")
            sys.stdout.flush()
        if not a.watch:
            return 0 if trees else 1
        time.sleep(a.watch)


if __name__ == "__main__":
    sys.exit(main())
