"""`orchestra update done`: post something you finished to the operator's Updates list (attention part 1).

    orchestra update done "what is finished, in one line"
        [--summary MD] [--evidence-test "12 passed"] [--evidence-url Label=URL ...] [--verified] [--key K]

A done note is not a question: decisions still go on a card. It lands in the Updates list every surface
reads (GET /attention). While the phone and watch still read /pending-approvals ([attention]
legacy_completion_cards, until part 5), each note also files its linked completion card, exactly what
`approval.py complete` files, so the installed apps keep showing it.

Who you are: $ORCHESTRA_AGENT_ID, else the tmux session you run in (react_cmd.agent_id). --from may only
name yourself (your rotated -genN name counts). --key makes a retry safe: the same key never posts twice;
without it each run is a new note.

Exit 0 posted (the event id is printed), 1 the ledger is busy (run it again with the same --key),
2 nothing to post or no identity, 3 the attention store is not armed, 4 not you.
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

from .react_cmd import agent_id

_GEN = re.compile(r"-gen\d+$")
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _canon(name: str) -> str:
    return _GEN.sub("", (name or "").strip())


def _fill(p) -> None:
    p.add_argument("text", help="what is finished, in one line")
    p.add_argument("--summary", default=None, help="markdown detail (what changed, where to look)")
    p.add_argument("--evidence-test", dest="evidence_test", default=None, help='test result, e.g. "12 passed"')
    p.add_argument("--evidence-url", dest="evidence_url", action="append", default=[],
                   help="Label=URL (repeatable)")
    p.add_argument("--verified", action="store_true", help="you checked it by effect")
    p.add_argument("--from", dest="from_agent", default=None, help="yourself (optional; never another agent)")
    p.add_argument("--key", default=None, help="idempotency key: a retry with the same key posts nothing new")


def add_parser(sub) -> None:
    u = sub.add_parser("update", help="post to the operator's Updates list (e.g. something you finished)")
    verbs = u.add_subparsers(dest="update_cmd", required=True)
    _fill(verbs.add_parser("done", help="something the operator asked for is finished (not a question)"))


def _notify(rid) -> None:
    """Best effort, as `approval.py complete` does: a push for the linked card; the backstop re-notifies."""
    try:
        import approval_notify
        approval_notify.notify(rid)
    except Exception as e:  # noqa: BLE001
        print(f"orchestra update: notify deferred to backstop: {e}", file=sys.stderr)


def done(ns) -> int:
    text = (ns.text or "").strip()
    if not text:
        print("orchestra update done: nothing to post (give the one-line text)", file=sys.stderr)
        return 2
    me = agent_id()
    if not me:
        print("orchestra update done: who are you? set ORCHESTRA_AGENT_ID or run inside your tmux session",
              file=sys.stderr)
        return 2
    if ns.from_agent and _canon(ns.from_agent) != _canon(me):
        print(f"orchestra update done: --from {ns.from_agent} is not you ({me})", file=sys.stderr)
        return 4
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import attention_config
    import attention_store as S
    st = S.AttentionStore()
    st.migrate()
    if not st.armed:
        print(f"orchestra update done: the Updates list is not switched on yet ({S.MIGRATION_ID} is not "
              f"armed); nothing was posted", file=sys.stderr)
        return 3
    urls = []
    for u in ns.evidence_url or []:
        label, _, url = u.partition("=")
        urls.append({"label": label.strip(), "url": url.strip()} if url else {"label": "Link", "url": u.strip()})
    evidence = {"tests": ns.evidence_test, "urls": urls, "verified": bool(ns.verified)}
    key = ns.key or uuid.uuid4().hex
    try:
        if attention_config.load().legacy_completion_cards:
            # Card first, then the row: a crash in between leaves a pending card the detector's mirror
            # picks up, so nothing is lost. The op_key makes a retry with the same --key re-find the card,
            # whatever its status and however long ago it was filed (create()'s own guard only covers a
            # pending twin and a 15-minute window, so an accepted card would otherwise be filed again).
            from approval_schema import ApprovalStore
            ledger = ApprovalStore()
            ledger.migrate()
            op_key = f"done:{me}:{key}"
            prior = [r for r in ledger.find_by_op_key(op_key) if r.get("from_agent") == me] if ns.key else []
            if prior:
                rid = prior[-1]["id"]                       # the first card filed under this key
                eid = st.add_done(me, text, key=key, body=ns.summary, evidence=evidence, source_id=rid)
            else:
                rid = ledger.create(from_agent=me, question=text, worker_kind="pane", op_key=op_key,
                                    options=["accept", "respond"], summary=ns.summary, kind="completion",
                                    evidence=evidence)
                eid = st.add_done(me, text, key=key, body=ns.summary, evidence=evidence, source_id=rid)
                _notify(rid)
        else:
            eid = st.add_done(me, text, key=key, body=ns.summary, evidence=evidence)
    except S.AttentionNotArmed as e:
        print(f"orchestra update done: {e}", file=sys.stderr)
        return 3
    except S.AttentionBusy:
        print(f"orchestra update done: the ledger is busy, run it again with --key {key}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — a locked ledger on the card side is the same story
        if "locked" in str(e).lower():
            print(f"orchestra update done: the ledger is busy, run it again with --key {key}", file=sys.stderr)
            return 1
        raise
    print(eid)
    return 0


def cmd_update(ns) -> int:
    if getattr(ns, "update_cmd", None) == "done":
        return done(ns)
    print("orchestra update: say what kind of update (done)", file=sys.stderr)
    return 2


def main_done(argv) -> int:
    """`orchestra update done ARGV...` without the top-level CLI (tests, scripts)."""
    import argparse
    p = argparse.ArgumentParser(prog="orchestra update done")
    _fill(p)
    return done(p.parse_args(argv))
