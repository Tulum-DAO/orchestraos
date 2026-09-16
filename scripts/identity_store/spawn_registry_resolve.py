#!/usr/bin/env python3
"""Spawn-path DB-first CLI (DEC-1788603298) — the shell entrypoint spawn-agent.sh's
get_agent_field falls back to when the flat registry.json has flapped an agent out under
the identity-store cutover. Read-only; resolves ONE agent's spawnable registry record
from the DB and emits it for the shell to cache (resolve-once, C4).

Usage:
  spawn_registry_resolve.py --dump  <agent_id>   # all fields as TAB-separated key\\tvalue
  spawn_registry_resolve.py --field <agent_id> <field>   # one field (tests/adhoc)

Exit 0 + output when the agent is a spawnable (canonical|provisional) DB record;
exit 1 when it is absent / retired / the DB is unreadable (the shell then keeps its
current flat-miss behavior). A JSON-null or missing field emits '' (NOT the literal
"None" — C3); a list emits space-joined (get_agent_field parity).
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _fmt(v):
    """Match get_agent_field's output shape, with the C3 null fix."""
    if v is None:
        return ""                       # [C3] json null -> '' (not "None")
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v)


def main(argv):
    """Accept BOTH the flagged form (``--dump <id>`` / ``--field <id> <field>``) that
    get_agent_field's resolve-once cache uses, AND the bare-positional form
    (``<agent_id> [field]``) — one arg dumps the record, two prints that one field.
    The positional ``<id> <field>`` form mirrors get_agent_field's own signature so a
    direct caller (or a by-effect check) resolves the field without needing a mode flag."""
    if not argv:
        sys.stderr.write("usage: spawn_registry_resolve.py [--dump|--field] <agent_id> [field]\n")
        return 2
    if argv[0] in ("--dump", "--field"):
        mode, rest = argv[0], argv[1:]
    else:  # bare positional: <agent_id> [field]
        mode, rest = ("--field" if len(argv) >= 2 else "--dump"), argv
    if not rest:
        sys.stderr.write("usage: spawn_registry_resolve.py [--dump|--field] <agent_id> [field]\n")
        return 2
    agent_id = rest[0]
    orch = os.environ.get("ORCHESTRA_DIR", _REPO)
    from scripts.identity_store import resolver
    # alarm is best-effort inside resolver; a spawn-path resolve should never emit noise
    # to stderr that the shell might capture — keep it silent here.
    rec = resolver.registry_agent_db(orch, agent_id, alarm=lambda _m: None)
    if rec is None:
        return 1
    if mode == "--dump":
        for k, v in rec.items():
            # TAB-delimited; collapse any stray newline in a value so line-parsing holds.
            sys.stdout.write(f"{k}\t{_fmt(v).replace(chr(10), ' ')}\n")
        return 0
    # --field: needs a field name (rest[1] for positional, argv[2] for flagged)
    if len(rest) < 2:
        sys.stderr.write("--field requires a field name\n")
        return 2
    sys.stdout.write(_fmt(rec.get(rest[1], "")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
