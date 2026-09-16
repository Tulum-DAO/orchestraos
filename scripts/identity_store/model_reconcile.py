"""generations.model truth reconcile — Identity Layer v1 (gm msg_07fbad31 + addendum
msg_df23bfaa). The migrate 'unknown' class: 213 generations carry model NULL/''/'unknown'
(the collect.py ctx% 1.43 root cause — an unknown model made the jsonl ctx fallback poison).

For each canonical row with a NON-retired generation and model in (NULL,'','unknown'),
resolve the model BY EFFECT, in PRECEDENCE (never guess):
  (a) the live sid transcript ``~/.claude/projects/<dir>/<sid>.jsonl`` newest type=assistant
      entry's ``model`` field;
  (b) the pane pid-tree cmdline ``--model X`` if present;
  (c) codex rollout session_meta model / gemini declared model (runtime meta);
  (d) else leave 'unknown' + LOUD reason=model:unresolvable.

SERVICE rows are set to the explicit constant 'n/a' (enum-style), NOT unknown, so the guard
excludes them by value. PROVISIONAL rows are LEFT to the bg lane. Writes go through the
sanctioned ``identity_writer.update_session(fields={'model':X})`` (the ADDENDUM added 'model'
to the mutable set + a write-time assert), then project_now. Dry-run by default; ``--apply``.
"""
import argparse
import json
import os
import subprocess

from scripts.identity_store import identity_writer, orchestra_db

UNKNOWN = (None, "", "unknown")
_LLM = ("claude", "codex", "gemini")


def _db_path(od):
    return os.path.join(od, "state", "orchestra-registry.db")


def _model_from_transcript(sid):
    if not sid:
        return None
    try:
        from scripts import sid_invariants as SI
        path = SI.find_transcript(sid)
    except Exception:
        path = None
    if not path or not os.path.exists(str(path)):
        return None
    model = None
    try:
        with open(str(path)) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == "assistant":
                    m = (o.get("message", {}) or {}).get("model") or o.get("model")
                    if m:
                        model = m
    except OSError:
        return None
    return model


def _model_from_cmdline(tmux):
    """(b) pane pid-tree cmdline --model X."""
    if not tmux:
        return None
    r = subprocess.run(["tmux", "list-panes", "-t", tmux, "-F", "#{pane_pid}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    pids = r.stdout.split()
    seen = set()
    while pids:
        pp = pids.pop().strip()
        if not pp or pp in seen:
            continue
        seen.add(pp)
        try:
            with open(f"/proc/{pp}/cmdline", "rb") as fh:
                args = fh.read().decode(errors="replace").split("\x00")
        except OSError:
            args = []
        for i, a in enumerate(args):
            if a == "--model" and i + 1 < len(args) and args[i + 1]:
                return args[i + 1]
            if a.startswith("--model="):
                return a.split("=", 1)[1]
        kids = subprocess.run(["pgrep", "-P", pp], capture_output=True, text=True)
        pids += kids.stdout.split()
    return None


def resolve_model(row):
    """Precedence (a)->(d). Returns (model_or_None, reason)."""
    m = _model_from_transcript(row["session_id"])
    if m:
        return m, "transcript"
    m = _model_from_cmdline(row["tmux"])
    if m:
        return m, "cmdline"
    # (c) runtime meta: codex/gemini declared model on the generation row's note/doc.
    if row.get("declared_model"):
        return row["declared_model"], "runtime-meta"
    return None, "model:unresolvable"


def reconcile(orchestra_dir, *, apply=False):
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT c.root AS root, c.tmux_session AS tmux, c.status AS status, "
            "l.runtime AS runtime, g.session_id AS session_id, g.model AS model, "
            "g.note AS declared_model, g.promoted_at AS promoted_at "
            "FROM canonical c JOIN generations g ON g.id=c.generation_id "
            "LEFT JOIN lineages l ON l.root=c.root "
            "WHERE g.retired_at IS NULL")]
    finally:
        conn.close()

    resolved, unresolved, services, skipped = [], [], [], []
    for r in rows:
        if r["model"] not in UNKNOWN:
            continue
        rt = r["runtime"]
        if rt == "service":
            services.append(r["root"])
            continue
        if r["status"] == "provisional":
            skipped.append(r["root"])       # LEAVE provisional to the bg lane
            continue
        if rt not in _LLM:
            skipped.append(r["root"])
            continue
        model, reason = resolve_model(r)
        if model:
            resolved.append({"root": r["root"], "runtime": rt, "model": model,
                             "reason": reason})
        else:
            unresolved.append({"root": r["root"], "runtime": rt,
                               "reason": reason})

    if apply:
        for item in resolved:
            identity_writer.update_session(orchestra_dir, item["root"],
                                           {"model": item["model"]})
        for root in services:
            identity_writer.update_session(orchestra_dir, root, {"model": "n/a"})
        if resolved or services:
            identity_writer.project_now(orchestra_dir)

    return {"applied": bool(apply), "scanned": len(rows),
            "resolved": resolved, "unresolved": unresolved,
            "services": services, "skipped_provisional_or_nonllm": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser(description="generations.model truth reconcile (dry-run default)")
    ap.add_argument("--dir", default=os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    rep = reconcile(a.dir, apply=a.apply)
    by_rt = {}
    for it in rep["resolved"]:
        by_rt.setdefault(it["runtime"], {}).setdefault(it["reason"], 0)
        by_rt[it["runtime"]][it["reason"]] += 1
    print(json.dumps({
        "applied": rep["applied"], "scanned": rep["scanned"],
        "resolved_count": len(rep["resolved"]), "resolved_by_runtime_reason": by_rt,
        "unresolved_count": len(rep["unresolved"]),
        "unresolved": rep["unresolved"][:20],
        "services_set_na": len(rep["services"]),
        "skipped_provisional_or_nonllm": len(rep["skipped_provisional_or_nonllm"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
