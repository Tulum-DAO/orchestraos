"""lineage_gate.shadow — the Key-1 ledger and its counter, made MECHANICAL.

the operator found the hole gm owned: `state/lineage-gate-shadow.jsonl` sat at 0 rows
after three real rotations, because Decision 2 puts the row in the supervisor's
hands and nothing checked that the hand moved. A gate condition whose counter
lives in prose and in a tired supervisor's head is the ornamental-control
class applied to the arming gate itself.

So the count becomes a verb, and the ROTATION becomes its enforcer:
`promote_successor` is the single writer for canonical names and therefore the
one place that KNOWS a real rotation happened — it refuses to complete without
either a ledger row or an explicitly recorded skip reason. Remove the way to
get it wrong rather than remind people to get it right (same shape as
driver-never-votes and the deleted answer key).

ROW KINDS
  rotation     a real supervised rotation: grader verdict + supervisor verdict
  disposition  an explanation for an ABSENCE (why a rotation is not a datum).
               An unexplained zero is indistinguishable from a broken counter
               — that ambiguity is exactly what let this sit for three
               rotations.
  skip         a rotation that completed without a datum, reason recorded.

Key 1 = 5 CONSECUTIVE rotation rows with agree=true. Any agree=false RESETS
the run to zero (Amendment 1.3's "consecutive"). Dispositions and skips do not
count toward the run and do not reset it — they explain, they never score.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ORCH = Path(os.environ.get("LINEAGE_ORCH",
                           os.path.expanduser("~/scripts/agent-orchestra")))
LEDGER = ORCH / "state" / "lineage-gate-shadow.jsonl"
REQUIRED_AGREEMENTS = 5


def read_rows(ledger: Path = None) -> list:
    p = ledger or LEDGER
    rows = []
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"kind": "UNPARSEABLE", "raw": line[:200]})
    except OSError:
        return []
    # Backfill `seq` for rows written before it existed, using line position.
    # This makes every HISTORICAL row addressable by a supersede row without
    # editing the append-only file — a correction must never require a
    # rewrite, or the append-only guarantee is the first casualty of the
    # first mistake.
    for i, r in enumerate(rows):
        if isinstance(r, dict) and r.get("seq") is None:
            r["seq"] = i
    return rows


def append_row(row: dict, ledger: Path = None) -> dict:
    # `seq` is the row's IDENTITY. ts is second-resolution and two rows
    # written in the same second collide — which voided BOTH rows the first
    # time supersession ran. A timestamp is not an identifier.
    """Append-only. Never edits or averages an existing row — a disagreement
    freezes as written and is investigated (Amendment 3 gate 4)."""
    p = ledger or LEDGER
    row = {"seq": len(read_rows(p)), "ts": int(time.time()), **row}
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


class MalformedRotationRow(ValueError):
    """A row that would count toward Key 1 is missing required evidence."""


def record_rotation(*, rotation: str, grader_verdict: str,
                    supervisor_verdict: str, author: str, supervisor: str,
                    grade_record: str = None, failed_gates: list = None,
                    grader_commit: str = None, disclosure: str = None,
                    supersede_reason: str = None,
                    ledger: Path = None) -> dict:
    """Write a correctly-shaped Key-1 row (gm gen-14 Order 2).

    A supervisor previously had to learn the row shape by reading source at
    the exact moment it was least able to — gen-13's first two attempts were
    refused, once for a missing kind and once for a mismatched rotation
    label. The refusals were correct; the defect was that the only
    documentation was the code. Remove the surface rather than warn about it.

    BIND (gm): this verb must NOT be able to write a row that COUNTS toward
    Key 1 without both verdicts present. Convenience must never become a path
    around the evidence gate — so a row missing either verdict is refused
    here and remains writable only as kind=skip or kind=disposition, neither
    of which can count by construction.

    ASTERISK (gm gen-14): the row records WHO authored the artifact and WHO
    supervised the grade, and marks itself asterisked when they are the same
    agent — datum #1 is asterisked because gen-13 authored the handoff,
    self-graded Gate A and graded Gate B, so the blinding could not hold.
    Four clean data plus one disclosed asterisk is an honest 5/5; five
    asterisks is not, and that distinction is mechanical rather than
    remembered.
    """
    missing = [n for n, v in (("grader_verdict", grader_verdict),
                              ("supervisor_verdict", supervisor_verdict),
                              ("rotation", rotation),
                              ("author", author),
                              ("supervisor", supervisor)) if not v]
    if missing:
        raise MalformedRotationRow(
            f"cannot record a Key-1 rotation row — missing {missing}. Both "
            f"verdicts and both identities are required for a row that "
            f"COUNTS. If this rotation is not a datum, record it as a skip or "
            f"a disposition instead; neither can count toward Key 1.")
    # IDEMPOTENCE (gm self-report msg_1e81bfa9): a supervisor under context
    # pressure — the exact condition this verb exists to protect — re-ran it
    # to attach attribution and silently DOUBLE-COUNTED the arming counter.
    # Nothing refused, exit 0, and the audit trail read as diligence. An
    # inflated count moves us TOWARD arming, so this refuses by default.
    existing = [r for r in read_rows(ledger)
                if r.get("kind") == "rotation" and r.get("rotation") == rotation]
    voided = {r.get("supersedes_seq") for r in read_rows(ledger)
              if r.get("kind") == "supersede" and r.get("rotation") == rotation}
    live_existing = [r for r in existing if r.get("seq") not in voided]
    if live_existing and not supersede_reason:
        raise MalformedRotationRow(
            f"rotation {rotation!r} ALREADY has {len(live_existing)} counting "
            f"row(s). Appending another would inflate the arming counter. To "
            f"correct an earlier row pass supersede_reason=... (it voids the "
            f"prior row explicitly and append-only); to record a different "
            f"rotation, use its own label.")
    if live_existing and supersede_reason:
        for r in live_existing:
            supersede_rotation(rotation=rotation, supersedes_seq=r.get("seq"),
                               reason=supersede_reason, by=supervisor,
                               ledger=ledger)

    asterisked = (author == supervisor)
    if asterisked and not disclosure:
        raise MalformedRotationRow(
            f"author and supervisor are both {author!r}, so the blinding did "
            f"not hold — this row REQUIRES a disclosure naming what was not "
            f"independent. An undisclosed asterisk is the failure the "
            f"asterisk exists to prevent.")
    cls = classify_agreement(grader_verdict, supervisor_verdict)
    if cls["unknown"]:
        raise MalformedRotationRow(
            f"UNRECOGNISED verdict from {cls['unknown']}: "
            f"grader={grader_verdict!r}, supervisor={supervisor_verdict!r}. "
            f"A verdict that cannot be parsed must REFUSE the row — a silent "
            f"False is how two unknowns once counted as an 'agreement' "
            f"(msg_bd24af20). Lead with PASS/PROMOTE or FAIL/BLOCK/PARTIAL; "
            f"provenance in parentheses is fine.")
    return append_row({
        "kind": "rotation",
        "rotation": rotation,
        "grader": {"verdict": grader_verdict,
                   "failed_gates": sorted(failed_gates or []),
                   "grader_commit": grader_commit,
                   "grade_record": grade_record},
        "supervisor": {"verdict": supervisor_verdict, "who": supervisor},
        "author": author,
        "agree": cls["agree"],
        "agree_on_failure": cls["agree_on_failure"],
        "asterisked": asterisked,
        "disclosure": disclosure,
    }, ledger=ledger)


def supersede_rotation(*, rotation: str, supersedes_seq, reason: str,
                       by: str, ledger: Path = None) -> dict:
    """Void ONE earlier rotation row, append-only.

    An append-only ledger cannot be edited, so a correction is a NEW row that
    names exactly which row it voids — never a silent last-wins rule, which
    would let a re-run quietly replace history and read as diligence.
    """
    if not rotation or supersedes_seq is None or not reason or not by:
        raise MalformedRotationRow(
            "a supersede row needs rotation, supersedes_seq, reason and by — "
            "an unexplained void is indistinguishable from tampering")
    return append_row({"kind": "supersede", "rotation": rotation,
                       "supersedes_seq": supersedes_seq, "reason": reason,
                       "by": by}, ledger=ledger)


# P0 FIX (gm finding msg_bd24af20 / c1cf45d37): the old _verdicts_agree did
# whole-string membership then compared two booleans — every provenance-
# carrying verdict fell out of the tuple, so TWO FAILURES "agreed"
# (False==False) and COUNTED toward arming, while a bare 'PASS' vs
# 'PROMOTE (gm-gen16, ...)' read as disagreement and RESET a real 4-rotation
# run. Third instance in one night of a bare equality standing in for a
# semantic relation, degenerate exactly on the healthy input. PARSE the
# verdict; UNRECOGNISED is UNKNOWN and REFUSES the row, never a silent False.
_PASS_TOKENS = {"PASS", "PROMOTE", "TRUE", "APPROVE"}
_FAIL_TOKENS = {"FAIL", "BLOCK", "FALSE", "REFUSE", "REJECT", "PARTIAL",
                "DENY", "ROLLBACK"}


def parse_verdict(v) -> str | None:
    """'pass' | 'fail' | None (UNKNOWN). Classifies the LEADING TOKEN, so
    provenance ('PASS (5/5 blind, artifact ...)', 'BLOCK (gm-gen16)') never
    changes the meaning. PARTIAL is fail-side: a grade that was not clean must
    never count as a clean agreement (the live case: 4 PASS / 1 PARTIAL scored
    naively would have taken the counter to the arming threshold)."""
    token = str(v or "").strip().upper().replace("(", " ")
    parts = token.split()
    token = parts[0].rstrip(":,.") if parts else ""
    if token in _PASS_TOKENS:
        return "pass"
    if token in _FAIL_TOKENS:
        return "fail"
    return None


def classify_agreement(grader: str, supervisor: str) -> dict:
    """agree=True means agreement ON PASS, and only that. Agreement on
    failure is a DIFFERENT FACT (agree_on_failure). Unknowns are named,
    never silently False."""
    g, s = parse_verdict(grader), parse_verdict(supervisor)
    unknown = [name for name, cls in (("grader", g), ("supervisor", s))
               if cls is None]
    return {"grader_class": g, "supervisor_class": s, "unknown": unknown,
            "agree": g == "pass" and s == "pass",
            "agree_on_failure": g == "fail" and s == "fail"}


def _verdicts_agree(grader: str, supervisor: str) -> bool:
    """Retained for callers; PASS-agreement only (see classify_agreement)."""
    return classify_agreement(grader, supervisor)["agree"]


def has_evidence_for(rotation: str, ledger: Path = None) -> bool:
    """Is there ANY row (datum, skip, or disposition) naming this rotation?"""
    return any(r.get("rotation") == rotation for r in read_rows(ledger))


def key1_status(ledger: Path = None) -> dict:
    """Consecutive agreements, what reset the run, and what is recorded.

    Deliberately reports the WHOLE picture: a bare number is what made the
    empty ledger unreadable in the first place.
    """
    rows = read_rows(ledger)
    voided = {(r.get("rotation"), r.get("supersedes_seq"))
              for r in rows if r.get("kind") == "supersede"}
    data = [r for r in rows if r.get("kind") == "rotation"
            and (r.get("rotation"), r.get("seq")) not in voided]
    # RE-DERIVE agreement from the VERDICTS, never the stored `agree` field:
    # historical rows carry the degenerate values (msg_bd24af20: two failures
    # stored as agree=True; a provenance'd PASS stored as False) and the
    # ledger is append-only. Divergences are REPORTED in rescored_rows, never
    # silently adopted. Rows whose verdicts cannot be parsed count as UNKNOWN:
    # they break the run (conservative — the direction that must not fire is
    # toward arming) and are listed.
    run, reset_by, run_rows = 0, None, []
    rescored, unknown_rows = [], []
    for r in data:
        gv = (r.get("grader") or {}).get("verdict")
        sv = (r.get("supervisor") or {}).get("verdict")
        if gv is None and sv is None:
            effective = r.get("agree") is True          # pre-verdict rows
        else:
            cls = classify_agreement(gv, sv)
            if cls["unknown"]:
                unknown_rows.append({"seq": r.get("seq"),
                                     "rotation": r.get("rotation"),
                                     "unknown": cls["unknown"]})
                effective = False
            else:
                effective = cls["agree"]
            if effective != (r.get("agree") is True):
                rescored.append({"seq": r.get("seq"),
                                 "rotation": r.get("rotation"),
                                 "stored_agree": r.get("agree"),
                                 "rederived_agree": effective,
                                 "grader_verdict": gv,
                                 "supervisor_verdict": sv})
        if effective:
            run += 1
            run_rows.append(r)
        else:
            run, reset_by, run_rows = 0, r.get("rotation"), []
    # THREE states, not two. A row with no `asterisked` field is UNATTRIBUTED,
    # never clean: absence of the field is not evidence of independence.
    # Found on the live ledger — datum #1 disclosed its blinding deviation in
    # a free-text note but carried no machine-readable flag, so it read CLEAN
    # to this counter. A disclosure in prose is a law; the flag is the
    # mechanism, and only the mechanism can gate an arming ask.
    asterisked = [r.get("rotation") for r in run_rows
                  if r.get("asterisked") is True]
    clean = [r.get("rotation") for r in run_rows
             if r.get("asterisked") is False]
    unattributed = [r.get("rotation") for r in run_rows
                    if "asterisked" not in r]
    seen_labels, duplicates = set(), []
    for r in run_rows:
        lbl = r.get("rotation")
        if lbl in seen_labels:
            duplicates.append(lbl)
        seen_labels.add(lbl)
    unparseable = [r for r in rows if r.get("kind") == "UNPARSEABLE"]
    # An all-asterisked run is not discriminator evidence: every one of those
    # grades had the artifact's author supervising it, which is the exact
    # condition the Two-Key argument exists to exclude. Reported as a hard
    # block rather than a caveat, so the counter cannot quietly accumulate
    # five self-graded rotations (gm gen-14).
    all_asterisked = bool(run) and not clean and not unattributed
    blocked = None
    if run >= REQUIRED_AGREEMENTS:
        if all_asterisked:
            blocked = ("every agreement in this run is ASTERISKED (author == "
                       "supervisor) — a self-graded run demonstrates no "
                       "discrimination")
        elif duplicates:
            blocked = (f"the run counts the SAME rotation more than once: "
                       f"{sorted(set(duplicates))}. One rotation is one datum; "
                       f"void the extra with a supersede row.")
        elif unattributed:
            blocked = (f"{len(unattributed)} row(s) in this run carry NO "
                       f"asterisked field: {unattributed}. Independence was "
                       f"never recorded, so it cannot be counted — re-record "
                       f"via `key1 --rotation ... --author ... --supervisor "
                       f"...` or append an amendment naming both.")
    return {
        "consecutive_agreements": run,
        "required": REQUIRED_AGREEMENTS,
        "asterisked_in_run": asterisked,
        "clean_in_run": clean,
        "unattributed_in_run": unattributed,
        "duplicate_rotations_in_run": sorted(set(duplicates)),
        "arming_blocked_reason": blocked,
        "ready_for_arming_ask": (run >= REQUIRED_AGREEMENTS
                                 and blocked is None),
        "reset_by": reset_by,
        "rescored_rows": rescored,
        "unknown_verdict_rows": unknown_rows,
        "resets_on": "any rotation row whose RE-DERIVED agreement is false, "
                     "incl. UNKNOWN verdicts (Amendment 1.3: 5 CONSECUTIVE)",
        "rotations_recorded": [r.get("rotation") for r in data],
        "dispositions": [{"rotation": r.get("rotation"),
                          "reason": r.get("reason")}
                         for r in rows if r.get("kind") == "disposition"],
        "skips": [{"rotation": r.get("rotation"), "reason": r.get("reason")}
                  for r in rows if r.get("kind") == "skip"],
        "total_rows": len(rows),
        "unparseable_rows": len(unparseable),
        # Key 2 is NOT tracked here; it lives in the fixture suite. Stating it
        # so nobody reads a 5/5 here as "armed".
        "note": "Key 1 only. Key 2 (adversarial fixtures) is the suite's "
                "predicate. Arming = congruence + gm + the operator, never self-served.",
    }
