"""alarm_routing: an alarm ABOUT a seat is never delivered only to that seat.

A seat that has gone down cannot read an alarm addressed to itself. This module answers ONE
question, who hears an alarm about `seat`, from the identity DB (lineages.reports_to):

  * the named seat is NEVER in the answer;
  * its `reports_to` if set (and not itself), else DEFAULT_ESCALATION ('gm');
  * an alarm about the default escalation itself goes to ORCHESTRA_ROOT_ESCALATION when that
    env var names a seat, else to the owner channel alone;
  * OWNER_CHANNEL is ALWAYS the last element: the terminal hop is unconditional, because an agent
    recipient can be down too. It is a CHANNEL sentinel, not an agent id: callers render it
    (Telegram) and never hand it to msg_store as a recipient.

CONSUMERS call escalation_targets(seat) with one positional and use the list as returned, and
assert that call at import with inspect.signature(escalation_targets).bind("probe-seat").
Every read is best effort: an unreadable DB narrows to the default escalation, never to nobody.
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_ESCALATION = "gm"
ROOT_ESCALATION_ENV = "ORCHESTRA_ROOT_ESCALATION"
OWNER_CHANNEL = "owner:tg"
#: reports_to values that name the human owner rather than a seat (extend with ORCHESTRA_OWNER_ID).
OWNER_PARENTS = frozenset({"owner", "human"})


def _owner_parents() -> frozenset:
    extra = (os.environ.get("ORCHESTRA_OWNER_ID") or "").strip()
    return OWNER_PARENTS | ({extra} if extra else set())


def _default_dir() -> str:
    try:
        from orchestra_cli.settings import data_dir
        return str(data_dir())
    except Exception:  # noqa: BLE001 — no settings: the env, else nothing resolvable
        return os.environ.get("ORCHESTRA_DIR", "")


def _db(orchestra_dir: str) -> str:
    return os.path.join(orchestra_dir, "state", "orchestra-registry.db")


def _lineage_reports_to(orchestra_dir: str, root: str) -> str:
    con = sqlite3.connect(f"file:{_db(orchestra_dir)}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT reports_to FROM lineages WHERE root=?", (root,)).fetchone()
    finally:
        con.close()
    return (row[0] or "").strip() if row and row[0] else ""


def _agent_hops(orchestra_dir: str, named: str) -> list:
    root_escalation = (os.environ.get(ROOT_ESCALATION_ENV) or "").strip()
    if named == DEFAULT_ESCALATION:
        # the default escalation is the top of the tree: it has no parent to tell.
        return [root_escalation] if root_escalation and root_escalation != named else []
    try:
        parent = _lineage_reports_to(orchestra_dir, named)
    except Exception:  # noqa: BLE001 — an unreadable lineage must still produce an escalation
        parent = ""
    if parent in _owner_parents() or parent == named:
        # The owner is a person, not an inbox, and a seat that reports to itself is not an
        # escalation: either way the agent hop is the default escalation (the owner channel is
        # added unconditionally by escalation_targets).
        parent = ""
    return [parent or DEFAULT_ESCALATION]


def escalation_targets(seat, orchestra_dir=None) -> list:
    """Who hears an alarm about `seat`: agents first, OWNER_CHANNEL last, `seat` never present.
    Argument order is (seat, orchestra_dir) so a consumer can bind it as escalation_fn(seat)."""
    named = (seat or "").strip()
    hops = [t for t in _agent_hops(orchestra_dir or _default_dir(), named) if t and t != named]
    return hops + [OWNER_CHANNEL]
