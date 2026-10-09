"""Tier is hierarchy POSITION; role says what a seat does (DEC-1791574633518521).

The operator's rule (2026-10-09): no standalone T2s. An agent with no parent, or whose parent is a
T0, registers as T1; an agent under any other parent registers as that parent's T2. Before this,
every auto-registered agent was a T2 with no `reports_to`, so it showed up as a standalone row.

Tier used to carry two jobs on top of position: "T1" meant "run the PM startup briefing" and
"is not auto-retire eligible". A parentless one-off helper is T1 under the new rule but is not a
project manager, so those jobs now key on `role` instead:

    role_of(row) -> "pm" | "worker"

`role` is written on every NEW registration ("pm" only when asked for: AGENT_ROLE=pm, or the
starter team's pm seat). A row registered before this has no role, and reads as "pm" when it is
a T1 and "worker" otherwise, which is exactly what tier meant for it before. No registry is
rewritten to get there.

    tier_for(agent_id, parent, parent_known, parent_tier, requested) -> (tier, reports_to, warning)

  parent set, known, tier T0          -> T1, reports_to parent
  parent set, known, tier T1/T2/T3    -> T2, reports_to parent
  parent set, known, tier unreadable  -> T2, reports_to parent, plus a warning
  parent set, NOT a registered agent  -> refused: register the parent or unset PARENT_AGENT_ID
  no parent (blank, or the agent itself) -> T1, no reports_to
  requested T2 where the rule says T1 -> refused: a T2 needs a parent that is not T0
  requested T0 / T1 / T3              -> as requested (an explicit choice), reports_to parent if any

CLI (spawn-agent.sh): tier_rule.py role --role R --tier T  -> pm | worker (role_of)
                      tier_rule.py decide <agent_id> [--parent P --parent-known 0|1
    --parent-tier T] [--requested T] [--role R] [--fields]  -> one JSON line {tier, reports_to, role,
    warning} (or 0x1f-separated with --fields); exit 3 with the reason on stderr when refused.
"""
import argparse
import json
import sys

TIERS = ("T0", "T1", "T2", "T3")
ROLES = ("pm", "worker")


class TierRefused(ValueError):
    """The registration must not happen; the message says what to do instead."""


def _tier(v) -> str:
    s = str(v or "").strip().upper()
    return s if s in TIERS else ""


# A stored role that is ABSENT reads by the old tier meaning; these spellings are how "absent"
# arrives from the readers (get_agent_field prints a JSON null as None).
_ABSENT = ("", "none", "null")


def role_of(row) -> str:
    """'pm' or 'worker' (a closed vocabulary). An explicit role wins; a row from before roles
    existed reads as a PM exactly when it is a T1 (what tier meant for it then). Any other stored
    value is not trusted to mean pm: it reads as worker, with a warning on stderr."""
    row = row if isinstance(row, dict) else {}
    role = str(row.get("role") or "").strip().lower()
    if role in ROLES:
        return role
    if role not in _ABSENT:
        print(f"tier_rule: unknown role {row.get('role')!r} for {row.get('name') or 'a seat'}; "
              f"reading it as worker (roles: {', '.join(ROLES)})", file=sys.stderr)
        return "worker"
    return "pm" if _tier(row.get("tier")) == "T1" else "worker"


def tier_for(agent_id: str, parent: str | None, parent_known: bool, parent_tier,
             requested=None) -> tuple[str, str | None, str | None]:
    parent = (parent or "").strip()
    if parent == agent_id:
        parent = ""
    warning = None
    if parent and not parent_known:
        raise TierRefused(
            f"{agent_id}: parent '{parent}' is not a registered agent. Register it first, or unset "
            f"PARENT_AGENT_ID to start {agent_id} as a lead (T1).")
    if not parent:
        rule = "T1"
    else:
        ptier = _tier(parent_tier)
        if ptier == "T0":
            rule = "T1"
        else:
            rule = "T2"
            if not ptier:
                warning = (f"{agent_id}: parent '{parent}' has no readable tier; registering "
                           f"{agent_id} as its T2")
    req = _tier(requested)
    if str(requested or "").strip() and not req:
        raise TierRefused(f"{agent_id}: unknown tier '{requested}' (one of {', '.join(TIERS)})")
    if req == "T2" and rule == "T1":
        raise TierRefused(
            f"{agent_id}: a T2 needs a parent that is not a T0. Set PARENT_AGENT_ID=<its lead>, "
            f"or drop the T2 request and it registers as a lead (T1).")
    tier = req if req and req != "T2" else rule
    return tier, (parent or None), warning


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("agent_id")
    d.add_argument("--parent", default="")
    d.add_argument("--parent-known", default="0")
    d.add_argument("--parent-tier", default="")
    d.add_argument("--requested", default="")
    d.add_argument("--role", default="")
    d.add_argument("--fields", action="store_true",
                   help="tier, reports_to, role, warning separated by the ASCII unit separator (0x1f)")
    r = sub.add_parser("role", help="print role_of(row) for a row's stored role and tier")
    r.add_argument("--role", default="")
    r.add_argument("--tier", default="")
    a = ap.parse_args(argv)
    if a.cmd == "role":
        print(role_of({"role": a.role, "tier": a.tier}))
        return 0
    role = (a.role or "worker").strip().lower()
    if role not in ROLES:
        print(f"{a.agent_id}: unknown role '{a.role}' (one of {', '.join(ROLES)})", file=sys.stderr)
        return 3
    try:
        tier, reports_to, warning = tier_for(a.agent_id, a.parent, a.parent_known == "1",
                                             a.parent_tier, a.requested)
    except TierRefused as e:
        print(str(e), file=sys.stderr)
        return 3
    if a.fields:
        # 0x1f, not a tab: bash `read` merges runs of IFS whitespace, so an empty reports_to between
        # two tabs vanished and the role landed in its place.
        print("\x1f".join((tier, reports_to or "", role, warning or "")))
    else:
        print(json.dumps({"tier": tier, "reports_to": reports_to, "role": role, "warning": warning}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
