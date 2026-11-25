#!/usr/bin/env python3
"""The outward-action ledger: an approved outward action runs exactly what the card
showed, once.

Rule
  An approved outward action runs exactly what the card showed, once. An interrupted one becomes
  `uncertain` and is never re-run automatically. A changed material argument after approval is
  refused. A duplicate pending card for the same action is refused.

States   asked -> approved -> executing -> done | not_run | uncertain ; asked -> not_run
  Nothing ever moves out of `uncertain`. An operator `release` stamps released_* on the row (the
  state stays uncertain, the fact that it may have run is never erased) and lets a NEW card be
  filed for the same arguments.

Own SQLite file (D1): $OUTWARD_LEDGER_PATH, else <ORCHESTRA_DIR>/state/outward-actions.db. Both
that path and the approvals DB path are resolved at CALL time, never at import (the
approval_config trap: an import-time path makes a monkeypatched env look like isolation while a
test writes the live file). Every transition is one `UPDATE ... WHERE state=?` under BEGIN
IMMEDIATE; rowcount 0 is a refusal (LedgerRefused names the current state; the CLI exits 3 with
one stderr line).

CLI (executors):
  outward_ledger.py claim   --action A --material-args JSON --actor X [--pid N]
  outward_ledger.py finish  --hash H --state done|not_run --reason R
  outward_ledger.py sweep
  outward_ledger.py show    [--hash H | --card ID | --id ID]
  outward_ledger.py release --hash H --by AGENT --reason R
"""
import argparse
import hashlib
import json
import os
import random
import socket
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATES = ("asked", "approved", "executing", "done", "not_run", "uncertain")
#: a row in one of these states with released_at NULL holds the hash (the partial unique index, V4)
LIVE_STATES = ("asked", "approved", "executing", "uncertain")
POLICIES = ("once", "at_least_once")
DEFAULT_CLAIM_TTL_S = 21600
RELEASE_REASON_MAX = 200

# D8: the reason vocabulary. Server-composed, never an exception's text (an exception contributes
# its class name only). Clients render these verbatim.
REASON_ANSWER = "answer: {answer}"                 # the card was answered with something not in run_on
REASON_TEXT_ONLY = "answer: text only"             # a free-text answer (A1): valid, never a run
REASON_CARD = "card {status}"                      # card expired / retired / discarded / resolved_elsewhere / missing
REASON_PID_DEAD = "claim lost: pid dead"
REASON_PID_REUSED = "claim lost: pid reused"
REASON_TTL = "claim lost: ttl expired"
REASON_BEFORE_SEND = "{cls}: before send"          # notify: nothing left the process
REASON_DURING_SEND = "{cls}: during send"          # notify: the network call may have happened
REASON_RESEND = "resend after uncertain"           # V6a: the policy release of attempt 1

DDL = """
CREATE TABLE IF NOT EXISTS outward_actions (
  id                TEXT PRIMARY KEY,
  action_hash       TEXT NOT NULL,
  action            TEXT NOT NULL,
  material_args     TEXT NOT NULL,
  card_id           TEXT,
  run_on            TEXT,
  actor             TEXT,
  state             TEXT NOT NULL CHECK(state IN ('asked','approved','executing','done','not_run','uncertain')),
  reason            TEXT,
  policy            TEXT NOT NULL DEFAULT 'once',
  attempt           INTEGER NOT NULL DEFAULT 1,
  claimed_by        TEXT,
  claimed_pid       INTEGER,
  claimed_pid_start TEXT,
  claimed_host      TEXT,
  created_at        TEXT NOT NULL,
  approved_at       TEXT,
  claimed_at        TEXT,
  finished_at       TEXT,
  updated_at        TEXT NOT NULL,
  released_at       TEXT,
  released_by       TEXT,
  release_reason    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oa_live ON outward_actions(action_hash)
  WHERE state IN ('asked','approved','executing','uncertain') AND released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_oa_card ON outward_actions(card_id);
"""


class LedgerRefused(Exception):
    """A transition the ledger would not make. `state` is the row's current state ('none' when no
    live row exists for the hash); `reason` says why in the D8 vocabulary."""

    def __init__(self, state, reason):
        self.state = state
        self.reason = reason
        super().__init__(f"{reason} (state: {state})")


class DuplicateAction(LedgerRefused):
    """ask()/claim_direct(): a live-or-uncertain row already holds this hash."""


def _now():
    return datetime.now(tz=timezone.utc).isoformat()


def _gen_id():
    return f"oa_{random.randrange(16**8):08x}_{int(datetime.now(tz=timezone.utc).timestamp()*1000)%10**8}"


# --- paths (call time) -------------------------------------------------------------------------

def db_path():
    """$OUTWARD_LEDGER_PATH, else <ORCHESTRA_DIR>/state/outward-actions.db. Read on every call."""
    p = os.environ.get("OUTWARD_LEDGER_PATH")
    if p:
        return str(p)
    import approval_config
    return str(approval_config.orchestra_dir() / "state" / "outward-actions.db")


def _approval_store():
    """The approvals store, by the same APPROVAL_DB_PATH override approval.py honours. Imported here,
    not at module top: the ledger has readers that must not need the approvals DB."""
    from approval_schema import ApprovalStore
    return ApprovalStore(db_path=os.environ.get("APPROVAL_DB_PATH") or None)


def _conn():
    path = db_path()
    import approval_config
    approval_config.refuse_live_db_under_pytest(path)   # the same fence as the approvals store
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=30, isolation_level=None)   # explicit BEGIN IMMEDIATE below
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(DDL)
    return c


# --- canonical hash (V5) ----------------------------------------------------------------------

def _canon(obj, path="args"):
    if obj is None or obj is True or obj is False:
        return obj
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        raise ValueError(f"material args must not carry floats; pass a string ({path})")
    if isinstance(obj, int):
        return obj
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, (list, tuple)):
        return [_canon(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError(f"material args keys must be strings ({path}.{k!r})")
            out[unicodedata.normalize("NFC", k)] = _canon(v, f"{path}.{k}")
        return out
    raise ValueError(f"material args must be JSON (str, int, bool, null, list, object); got "
                     f"{type(obj).__name__} at {path}")


def canonical_json(args):
    """The one serialisation of material args: NFC strings, sorted keys, no whitespace, ASCII,
    fail-closed on floats/NaN and on anything that is not JSON. Lists keep their order (order is
    material). `None` and a missing key are distinct on purpose."""
    return json.dumps(_canon(args), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def canonical_hash(action, args):
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string")
    doc = json.dumps({"action": action, "args": _canon(args)}, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(doc.encode("ascii")).hexdigest()


# --- claim identity (V1) -----------------------------------------------------------------------

def pid_start(pid):
    """/proc/<pid>/stat field 22 (starttime, clock ticks since boot) as a string; None when the pid
    is gone or there is no /proc (then only the TTL can retire a claim)."""
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            raw = f.read()
    except (OSError, ValueError):
        return None
    try:
        return raw.rsplit(")", 1)[1].split()[19]
    except IndexError:
        return None


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def _claim_identity(pid=None, pid_start_=None, host=None):
    pid = os.getpid() if pid is None else int(pid)
    start = pid_start(pid) if pid_start_ is None else str(pid_start_)
    return pid, start, host or socket.gethostname()


# --- the card mirror (D4) + the uncertain card (D7) ------------------------------------------

def _mirror(row):
    """evidence.action = {hash, action, state, reason, updated_at} on the card, merged additively
    (ApprovalStore.merge_evidence, V2). Best effort: the ledger row is the truth, the mirror is a
    view; a failure is one stderr line, never an exception to the executor."""
    card_id = row.get("card_id")
    if not card_id:
        return
    view = {"hash": row["action_hash"], "action": row["action"], "state": row["state"],
            "reason": row.get("reason"), "updated_at": row.get("updated_at")}
    if row.get("released_at"):
        view["released_by"] = row.get("released_by")
        view["release_reason"] = row.get("release_reason")
    try:
        _approval_store().merge_evidence(card_id, "action", view)
    except Exception as e:  # noqa: BLE001
        print(f"[outward_ledger] card mirror for {card_id} failed ({type(e).__name__}): {e}", file=sys.stderr)


UNCERTAIN_OP_KEY = "oa-uncertain:"
_FILING_CARD = False      # re-entrancy guard: the D7 card's own push never files a D7 card


def _is_push_of_an_uncertain_card(row):
    """A notify.* row whose target is itself a D7 card. Its uncertain push must not file another
    card, or an outage would chain cards forever through the cron backstop."""
    if not str(row.get("action") or "").startswith("notify."):
        return False
    try:
        key = json.loads(row.get("material_args") or "{}").get("card_id") or ""
    except ValueError:
        return False
    card_id = key.rsplit(":", 1)[-1] if ":" in key else key     # telegram keys are '<channel>:<id>'
    if not card_id.startswith("apr_"):
        return False
    try:
        card = _approval_store().get(card_id)
    except Exception:  # noqa: BLE001
        return False
    return bool(card) and str(card.get("op_key") or "").startswith(UNCERTAIN_OP_KEY)


def _file_uncertain_card(row):
    """D7: an uncertain outcome must be seen. ONE kind='human_task' "Waiting on you" card from the
    executing agent, op_key oa-uncertain:<hash> (create() dedupes a pending twin, so a repeated
    sweep never files twice), verbs done/cant/snooze (contract V4). The same create() shape as
    `approval.py human-task`, then the existing push (the backstop retries a miss)."""
    global _FILING_CARD
    h = row["action_hash"]
    if _FILING_CARD or _is_push_of_an_uncertain_card(row):
        print(f"[outward_ledger] {row['action']} {h[:12]} uncertain: no second card for the push of an "
              f"uncertain card", file=sys.stderr)
        return None
    actor = row.get("actor") or "outward-ledger"
    task = f"Check: {row['action']} may have run, will not retry"
    summary = f"{row.get('card_id') or 'no card (direct send)'}: {row.get('reason') or 'uncertain'}"
    _FILING_CARD = True
    try:
        try:
            store = _approval_store()
            store.migrate()
            rid = store.create(from_agent=actor, question=task, worker_kind="pane",
                               op_key=f"{UNCERTAIN_OP_KEY}{h}", options=["done", "cant", "snooze"],
                               kind="human_task", block_task=task, blocks_what=actor, summary=summary)
        except Exception as e:  # noqa: BLE001
            print(f"[outward_ledger] could not file the uncertain card for {h}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return None
        try:
            from approval_notify import notify
            notify(rid, store=store)
        except Exception as e:  # noqa: BLE001
            print(f"[outward_ledger] uncertain card {rid} push deferred to backstop: {e}", file=sys.stderr)
        return rid
    finally:
        _FILING_CARD = False


def _after_uncertain(row):
    """Every uncertain row is mirrored onto its card. The D7 card is filed for every policy='once'
    row at once; a policy='at_least_once' row (notify.*) is surfaced by its ONE resend first (V6),
    so its card is filed only when attempt 2 also ends uncertain (V6a)."""
    _mirror(row)
    if row.get("policy") == "at_least_once" and int(row.get("attempt") or 1) < 2:
        print(f"[outward_ledger] {row['action']} {row['action_hash'][:12]} uncertain: one resend allowed "
              f"(policy at_least_once)", file=sys.stderr)
        return
    _file_uncertain_card(row)


# --- reads --------------------------------------------------------------------------------------

def _live_row(c, h):
    r = c.execute("SELECT * FROM outward_actions WHERE action_hash=? AND released_at IS NULL "
                  "AND state IN ('asked','approved','executing','uncertain') "
                  "ORDER BY attempt DESC LIMIT 1", [h]).fetchone()
    return dict(r) if r else None


def live_row(action=None, args=None, action_hash=None):
    """The row holding this hash (asked/approved/executing/unreleased uncertain), or None."""
    h = action_hash or canonical_hash(action, args)
    c = _conn()
    try:
        return _live_row(c, h)
    finally:
        c.close()


def show(action_hash=None, card_id=None, row_id=None):
    c = _conn()
    try:
        if row_id:
            q, p = "SELECT * FROM outward_actions WHERE id=?", [row_id]
        elif card_id:
            q, p = "SELECT * FROM outward_actions WHERE card_id=? ORDER BY created_at", [card_id]
        elif action_hash:
            q, p = "SELECT * FROM outward_actions WHERE action_hash=? ORDER BY created_at, attempt", [action_hash]
        else:
            q, p = "SELECT * FROM outward_actions ORDER BY created_at DESC LIMIT 200", []
        return [dict(r) for r in c.execute(q, p).fetchall()]
    finally:
        c.close()


def _row(c, h, attempt=None):
    if attempt is None:
        r = c.execute("SELECT * FROM outward_actions WHERE action_hash=? ORDER BY attempt DESC, created_at DESC "
                      "LIMIT 1", [h]).fetchone()
    else:
        r = c.execute("SELECT * FROM outward_actions WHERE action_hash=? AND attempt=?", [h, attempt]).fetchone()
    return dict(r) if r else None


def _refuse(c, h, fallback_reason):
    """Name the row's CURRENT state: the live row when one holds the hash, else the newest spent
    row (done / not_run / released uncertain), else 'none' (nothing was ever asked for these args)."""
    live = _live_row(c, h)
    if live is not None:
        raise LedgerRefused(live["state"], f"{h[:12]} is {live['state']}")
    last = _row(c, h)
    if last is None:
        raise LedgerRefused("none", fallback_reason)
    tag = "released uncertain" if last["state"] == "uncertain" else last["state"]
    raise LedgerRefused(last["state"], f"{h[:12]} is {tag}: {fallback_reason}")


# --- transitions (D3) --------------------------------------------------------------------------

def ask(action, args, card_id, actor, run_on=None):
    """File the row for a card: state asked. Refused (DuplicateAction) when a live-or-uncertain row
    holds the hash. `run_on` are the answers that mean run (option n for a menu card)."""
    run_on = list(run_on) if run_on else ["approve"]
    h = canonical_hash(action, args)
    now = _now()
    rid = _gen_id()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("INSERT INTO outward_actions (id, action_hash, action, material_args, card_id, run_on, actor, "
                      "state, policy, attempt, created_at, updated_at) VALUES (?,?,?,?,?,?,?,'asked','once',1,?,?)",
                      [rid, h, action, canonical_json(args), card_id, json.dumps(run_on), actor, now, now])
        except sqlite3.IntegrityError:
            live = _live_row(c, h)
            c.execute("ROLLBACK")
            raise DuplicateAction(live["state"] if live else "none",
                                  f"{h[:12]} already has a live row ({live['state'] if live else '?'})")
        c.execute("COMMIT")
        row = _row(c, h)
    finally:
        c.close()
    _mirror(row)
    return row


def repoint(action_hash, card_id):
    """Point an asked row at the card id create() actually returned (op_key dedupe)."""
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE outward_actions SET card_id=?, updated_at=? WHERE action_hash=? AND state='asked' "
                  "AND released_at IS NULL", [card_id, _now(), action_hash])
        c.execute("COMMIT")
        row = _row(c, action_hash)
    finally:
        c.close()
    if row:
        _mirror(row)
    return row


def _answer_key(answer, option_n, answer_text):
    """What the answer means to run_on: option_n for a menu pick, else the answer verb. Returns
    (key, text_only)."""
    if answer == "option":
        if option_n is None or str(option_n) == "":
            return None, True
        return str(option_n), False
    if answer in (None, "", "text"):
        return None, True
    return str(answer), False


def _apply_answer(c, row, answer, option_n=None, answer_text=None, now=None):
    """asked -> approved | not_run from a card answer. Returns the new row or None (rowcount 0)."""
    now = now or _now()
    key, text_only = _answer_key(answer, option_n, answer_text)
    try:
        run_on = json.loads(row.get("run_on") or "[]")
    except ValueError:
        run_on = []
    if key is not None and key in [str(x) for x in run_on]:
        res = c.execute("UPDATE outward_actions SET state='approved', approved_at=?, updated_at=? "
                        "WHERE id=? AND state='asked'", [now, now, row["id"]])
    else:
        reason = REASON_TEXT_ONLY if text_only else REASON_ANSWER.format(
            answer=" ".join(str(key).split())[:60])
        res = c.execute("UPDATE outward_actions SET state='not_run', reason=?, finished_at=?, updated_at=? "
                        "WHERE id=? AND state='asked'", [reason, now, now, row["id"]])
    if res.rowcount != 1:
        return None
    r = c.execute("SELECT * FROM outward_actions WHERE id=?", [row["id"]]).fetchone()
    return dict(r)


def on_card_answer(card_id, answer, option_n=None, answer_text=None):
    """The fast path, called by ApprovalStore.record_answer / record_batch_answer AFTER their own
    answer-once UPDATE committed. answer in run_on -> approved; anything else -> not_run. Never
    needed for correctness: sweep() reconciles the same thing from the card row (V3)."""
    c = _conn()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM outward_actions WHERE card_id=? AND state='asked' AND released_at IS NULL",
            [card_id]).fetchall()]
        out = []
        for row in rows:
            c.execute("BEGIN IMMEDIATE")
            new = _apply_answer(c, row, answer, option_n, answer_text)
            c.execute("COMMIT")
            if new:
                out.append(new)
    finally:
        c.close()
    for new in out:
        _mirror(new)
    return out


def claim(action, args, actor, pid=None, pid_start=None, host=None):
    """approved -> executing, by the hash of the args the executor is ABOUT to use. A changed
    material argument hashes to a row that does not exist -> refused "not approved". Records the
    caller's pid, its /proc starttime and the host (V1)."""
    h = canonical_hash(action, args)
    pid, start, host = _claim_identity(pid, pid_start, host)
    now = _now()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        res = c.execute("UPDATE outward_actions SET state='executing', claimed_by=?, claimed_pid=?, "
                        "claimed_pid_start=?, claimed_host=?, claimed_at=?, updated_at=? "
                        "WHERE action_hash=? AND state='approved' AND released_at IS NULL",
                        [actor, pid, start, host, now, now, h])
        if res.rowcount != 1:
            c.execute("ROLLBACK")
            _refuse(c, h, f"{h[:12]} not approved: no approved row for these arguments")
        c.execute("COMMIT")
        row = _live_row(c, h)
    finally:
        c.close()
    _mirror(row)
    return row


def claim_direct(action, args, actor, policy="once", pid=None, pid_start=None, host=None):
    """A policy-allowed send with no card (the notifier): insert approved+executing in one
    statement. A live-or-uncertain row for the hash -> refused. V6a: for policy='at_least_once' an
    unreleased uncertain attempt 1 is released by policy ('resend after uncertain') and attempt 2 is
    inserted in the same transaction; a second uncertain is refused thereafter."""
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    h = canonical_hash(action, args)
    pid, start, host = _claim_identity(pid, pid_start, host)
    now = _now()
    rid = _gen_id()
    attempt = 1
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        live = _live_row(c, h)
        if live is not None:
            if (policy == "at_least_once" and live["state"] == "uncertain" and live["attempt"] == 1
                    and live.get("policy") == "at_least_once"):
                c.execute("UPDATE outward_actions SET released_at=?, released_by='policy:at_least_once', "
                          "release_reason=?, updated_at=? WHERE id=? AND state='uncertain' AND released_at IS NULL",
                          [now, REASON_RESEND, now, live["id"]])
                attempt = 2
                print(f"[outward_ledger] {action} {h[:12]}: {REASON_RESEND} (attempt 2 of 2)", file=sys.stderr)
            else:
                c.execute("ROLLBACK")
                raise DuplicateAction(live["state"], f"{h[:12]} is {live['state']}")
        try:
            c.execute("INSERT INTO outward_actions (id, action_hash, action, material_args, actor, state, policy, "
                      "attempt, claimed_by, claimed_pid, claimed_pid_start, claimed_host, created_at, approved_at, "
                      "claimed_at, updated_at) VALUES (?,?,?,?,?,'executing',?,?,?,?,?,?,?,?,?,?)",
                      [rid, h, action, canonical_json(args), actor, policy, attempt, actor, pid, start, host,
                       now, now, now, now])
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")
            _refuse(c, h, f"{h[:12]} is held")
        c.execute("COMMIT")
        row = _row(c, h, attempt)
    finally:
        c.close()
    return row


def finish(action_hash, state, reason, actor=None):
    """executing -> done | not_run | uncertain. From executing only. `uncertain` here is the
    notifier's "the network call may have happened" (V6); it files the D7 card like a sweep does."""
    if state not in ("done", "not_run", "uncertain"):
        raise ValueError("finish state must be done, not_run or uncertain")
    reason = " ".join(str(reason or "").split())[:RELEASE_REASON_MAX]
    now = _now()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        res = c.execute("UPDATE outward_actions SET state=?, reason=?, finished_at=?, updated_at=? "
                        "WHERE action_hash=? AND state='executing' AND released_at IS NULL",
                        [state, reason, now, now, action_hash])
        if res.rowcount != 1:
            c.execute("ROLLBACK")
            _refuse(c, action_hash, f"{action_hash[:12]} is not executing")
        c.execute("COMMIT")
        row = _row(c, action_hash)
    finally:
        c.close()
    if state == "uncertain":
        _after_uncertain(row)
    else:
        _mirror(row)
    return row


def release(action_hash, by, reason):
    """The operator (or gm) retires an uncertain row so a NEW card can be filed. The state stays
    uncertain; released_at/released_by/release_reason are stamped (V4). The reason is the
    releaser's words: required, newlines stripped, max 200 chars (D8)."""
    reason = " ".join(str(reason or "").split())[:RELEASE_REASON_MAX]
    by = " ".join(str(by or "").split())[:80]
    if not reason:
        raise LedgerRefused("uncertain", "a release reason is required: say in plain words what you checked")
    if not by:
        raise LedgerRefused("uncertain", "--by is required: who is releasing")
    now = _now()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        res = c.execute("UPDATE outward_actions SET released_at=?, released_by=?, release_reason=?, updated_at=? "
                        "WHERE action_hash=? AND state='uncertain' AND released_at IS NULL",
                        [now, by, reason, now, action_hash])
        if res.rowcount != 1:
            c.execute("ROLLBACK")
            _refuse(c, action_hash, f"{action_hash[:12]} has no unreleased uncertain row")
        c.execute("COMMIT")
        r = c.execute("SELECT * FROM outward_actions WHERE action_hash=? AND released_at=? ORDER BY attempt DESC "
                      "LIMIT 1", [action_hash, now]).fetchone()
        row = dict(r)
    finally:
        c.close()
    print(f"[outward_ledger] {row['action']} {action_hash[:12]} released by {by}: {reason}", file=sys.stderr)
    _mirror(row)
    return row


# --- sweep (D3 + V1 + V3) ----------------------------------------------------------------------

def _claim_ttl_s():
    try:
        return int(os.environ.get("OUTWARD_CLAIM_TTL_S") or DEFAULT_CLAIM_TTL_S)
    except ValueError:
        return DEFAULT_CLAIM_TTL_S


def _claim_age_s(row, now_dt):
    try:
        t = datetime.fromisoformat(row.get("claimed_at") or row.get("created_at"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now_dt - t).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _lost_reason(row, now_dt, host):
    """Why an executing claim is lost, or None while it is live. Pid-checked only on the claiming
    host (a missing pid or a different starttime is dead); elsewhere only the TTL applies."""
    if row.get("claimed_host") == host and row.get("claimed_pid"):
        pid = row["claimed_pid"]
        if not _pid_alive(pid):
            return REASON_PID_DEAD
        start = pid_start(pid)
        if row.get("claimed_pid_start") and start and start != str(row["claimed_pid_start"]):
            return REASON_PID_REUSED
        if start is None and row.get("claimed_pid_start"):
            return REASON_PID_DEAD
    if _claim_age_s(row, now_dt) > _claim_ttl_s():
        return REASON_TTL
    return None


def _card_outcome(card):
    """What a card row says about its asked ledger row: ('answer', answer, option_n, text) when it
    carries an answer; ('closed', status) when it ended without one; None while pending."""
    if card is None:
        return ("closed", "missing")
    status = card.get("status")
    if status == "pending":
        return None
    if card.get("answer") is not None or status in ("answered", "resumed", "resume_failed", "stale_target"):
        return ("answer", card.get("answer"), card.get("option_n"), card.get("answer_text"))
    return ("closed", status or "missing")


def reconcile(now=None):
    """V3: every asked row against its card. Answered -> run_on match (approved / not_run);
    expired / retired / discarded / resolved_elsewhere / missing -> not_run("card <status>"). Heals a
    crash between the two DBs and an orphan from a failed create(). Returns {approved, not_run}."""
    out = {"approved": [], "not_run": []}
    c = _conn()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM outward_actions WHERE state='asked' AND released_at IS NULL").fetchall()]
    finally:
        c.close()
    if not rows:
        return out
    try:
        store = _approval_store()
        cards = store.get_many([r["card_id"] for r in rows if r.get("card_id")])
    except Exception as e:  # noqa: BLE001 — the approvals DB is not reachable: nothing to reconcile against
        print(f"[outward_ledger] reconcile skipped (approvals store): {type(e).__name__}: {e}", file=sys.stderr)
        return out
    changed = []
    c = _conn()
    try:
        for row in rows:
            verdict = _card_outcome(cards.get(row.get("card_id")))
            if verdict is None:
                continue
            c.execute("BEGIN IMMEDIATE")
            if verdict[0] == "answer":
                new = _apply_answer(c, row, verdict[1], verdict[2], verdict[3], now)
            else:
                stamp = now or _now()
                res = c.execute("UPDATE outward_actions SET state='not_run', reason=?, finished_at=?, updated_at=? "
                                "WHERE id=? AND state='asked'",
                                [REASON_CARD.format(status=verdict[1]), stamp, stamp, row["id"]])
                new = dict(c.execute("SELECT * FROM outward_actions WHERE id=?", [row["id"]]).fetchone()) \
                    if res.rowcount == 1 else None
            c.execute("COMMIT")
            if new:
                changed.append(new)
                out["approved" if new["state"] == "approved" else "not_run"].append(new["action_hash"])
    finally:
        c.close()
    for new in changed:
        _mirror(new)
    return out


def sweep(now=None):
    """executing -> uncertain when the claim is lost (pid dead / starttime differs on this host, or
    older than OUTWARD_CLAIM_TTL_S), then reconcile asked rows against their cards. Runs at the top
    of every approval.py and outward_ledger.py invocation. Returns {uncertain, approved, not_run}."""
    now_iso = now or _now()
    now_dt = datetime.fromisoformat(now_iso)
    host = socket.gethostname()
    out = {"uncertain": [], "approved": [], "not_run": []}
    c = _conn()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM outward_actions WHERE state='executing' AND released_at IS NULL").fetchall()]
        lost = []
        for row in rows:
            why = _lost_reason(row, now_dt, host)
            if why is None:
                continue
            c.execute("BEGIN IMMEDIATE")
            res = c.execute("UPDATE outward_actions SET state='uncertain', reason=?, finished_at=?, updated_at=? "
                            "WHERE id=? AND state='executing'", [why, now_iso, now_iso, row["id"]])
            c.execute("COMMIT")
            if res.rowcount == 1:
                lost.append(dict(c.execute("SELECT * FROM outward_actions WHERE id=?", [row["id"]]).fetchone()))
    finally:
        c.close()
    for row in lost:
        out["uncertain"].append(row["action_hash"])
        _after_uncertain(row)
    rec = reconcile(now_iso)
    out["approved"], out["not_run"] = rec["approved"], rec["not_run"]
    return out


# --- CLI ------------------------------------------------------------------------------------------

def _parse_args_json(raw):
    try:
        args = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"--material-args is not JSON: {e}")
    if not isinstance(args, dict):
        raise ValueError("--material-args must be a JSON object")
    canonical_json(args)                       # floats / non-str keys refused here
    return args


def main(argv=None):
    p = argparse.ArgumentParser(description="outward-action ledger (exit 3 = refused; one stderr line names the state)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("claim", help="approved -> executing for the EXACT args you are about to use")
    pc.add_argument("--action", required=True)
    pc.add_argument("--material-args", dest="material_args", required=True, help="JSON object; no floats")
    pc.add_argument("--actor", required=True)
    pc.add_argument("--pid", type=int, default=None,
                    help="the executing process (default: this CLI's parent, never the CLI itself)")
    pf = sub.add_parser("finish", help="executing -> done | not_run")
    pf.add_argument("--hash", required=True)
    pf.add_argument("--state", required=True, choices=["done", "not_run"])
    pf.add_argument("--reason", required=True)
    sub.add_parser("sweep", help="lost claims -> uncertain; asked rows reconciled against their cards")
    ps = sub.add_parser("show")
    ps.add_argument("--hash", default=None)
    ps.add_argument("--card", default=None)
    ps.add_argument("--id", default=None)
    pr = sub.add_parser("release", help="let a NEW card be filed for an uncertain action (state stays uncertain)")
    pr.add_argument("--hash", required=True)
    pr.add_argument("--by", required=True)
    pr.add_argument("--reason", required=True, help="what you checked, in plain words (max 200 chars)")
    a = p.parse_args(argv)

    if a.cmd != "sweep":
        try:
            sweep()
        except Exception as e:  # noqa: BLE001 — the sweep is a backstop, never a reason to block the verb
            print(f"[outward_ledger] sweep failed: {type(e).__name__}: {e}", file=sys.stderr)
    try:
        if a.cmd == "claim":
            args = _parse_args_json(a.material_args)
            pid = a.pid if a.pid is not None else os.getppid()
            row = claim(a.action, args, a.actor, pid=pid)
        elif a.cmd == "finish":
            row = finish(a.hash, a.state, a.reason)
        elif a.cmd == "sweep":
            row = sweep()
        elif a.cmd == "show":
            row = show(action_hash=a.hash, card_id=a.card, row_id=a.id)
        elif a.cmd == "release":
            row = release(a.hash, a.by, a.reason)
        else:  # pragma: no cover
            return 2
    except LedgerRefused as e:
        print(f"REFUSED: {e.reason} (state: {e.state})", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"[outward_ledger] {e}", file=sys.stderr)
        return 2
    print(json.dumps(row, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
