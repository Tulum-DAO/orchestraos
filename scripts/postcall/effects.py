"""External writes go through one interface (design §7): live, shadow or test.

An adapter is the outside world as the ledger sees it: send(effect) performs the write and returns
an external id; find(effect) looks the write up by its deterministic effect id (any status), so
recovery can tell "landed but unrecorded" from "never landed"."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Effect:
    effect_id: str
    kind: str
    payload: dict = field(default_factory=dict, compare=False)


class Recorder:
    """Test adapter: an in-memory outside world. Touches nothing outside the process."""

    def __init__(self):
        self.sent = []

    def send(self, effect):
        self.sent.append(effect)
        return f"ext-{len(self.sent)}"

    def find(self, effect):
        for n, e in enumerate(self.sent, 1):
            if e.effect_id == effect.effect_id:
                return f"ext-{n}"
        return None

    def card_answers(self, external_id):
        return "pending", {}


# --- mode + adapters ---------------------------------------------------------------------------
import json as _json
import os as _os
import re as _re
import sqlite3 as _sqlite3
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile

_LOCK_FD = None  # the sweep lock while a sweep holds it (set_lock_fd); passed to every CLI child


def set_lock_fd(fd):
    global _LOCK_FD
    _LOCK_FD = fd


def lock_fd():
    return _LOCK_FD


def _run(argv, **kw):
    """subprocess.run for a CLI child. Under a sweep the child inherits the lock fd (pass_fds), so the
    lock stays held until the last child exits even if the sweeper dies (design §1)."""
    if _LOCK_FD is not None:
        kw["pass_fds"] = tuple(kw.get("pass_fds") or ()) + (_LOCK_FD,)
    return _subprocess.run(argv, **kw)


AUTHOR = "postcall-dev"  # gm ruling msg_92221c61 §9.1; a service identity at rollout
_MODES = ("shadow", "live")
# The checkout this file lives in: <repo>/scripts/postcall/effects.py. The CLIs default to its copies.
REPO = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


def state_dir():
    return _os.environ.get("POSTCALL_STATE_DIR") or _os.path.expanduser("~/.orchestra/state/postcall")


def mode():
    """'live' only when gm has written exactly `live` to MODE; anything else (missing, unreadable,
    garbage) is shadow. POSTCALL_MODE=test is honoured for the test suite only."""
    if _os.environ.get("POSTCALL_MODE") == "test":
        return "test"
    try:
        with open(_os.path.join(state_dir(), "MODE")) as f:
            value = f.read().strip()
    except OSError:
        return "shadow"
    return value if value in _MODES else "shadow"


def ledger_path(which):
    d = _os.path.join(state_dir(), which)
    _os.makedirs(d, exist_ok=True)
    return _os.path.join(d, "ledger.db")


def msg_db_path():
    """The message bus DB (read-only use here: reconcile lookups and seat replies)."""
    if _os.environ.get("MSG_DB_PATH"):
        return _os.environ["MSG_DB_PATH"]
    base = _os.environ.get("ORCHESTRA_DIR") or _os.environ.get("ORCH_DIR") or _os.path.expanduser("~/.orchestra")
    return _os.path.join(base, "state", "tasks.db")


class Shadow:
    """Writes what WOULD be sent to shadow.jsonl. Never calls approval.py, msg_store or the network."""

    def __init__(self, path):
        self.path = path
        _os.makedirs(_os.path.dirname(path), exist_ok=True)

    def _rows(self):
        try:
            with open(self.path) as f:
                return [_json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def send(self, effect):
        ext = f"shadow-{len(self._rows()) + 1}"
        with open(self.path, "a") as f:
            f.write(_json.dumps({"effect_id": effect.effect_id, "kind": effect.kind, "external_id": ext,
                                 "payload": effect.payload}, sort_keys=True) + "\n")
        return ext

    def find(self, effect):
        for row in self._rows():
            if row["effect_id"] == effect.effect_id:
                return row["external_id"]
        return None

    def card_answers(self, external_id):
        return "pending", {}  # nobody answers a shadow card


class Live:
    """Files through the approval CLI (never by writing its DB). find() is a read-only lookup by op_key,
    any status, so recovery sees writes that landed but were never recorded (design §1)."""

    def __init__(self):
        self.cli = _os.environ.get("POSTCALL_APPROVAL_CLI") or _os.path.join(REPO, "scripts", "approval.py")
        self.msg_cli = _os.environ.get("POSTCALL_MSG_CLI") or _os.path.join(REPO, "msg_store.py")

    def _db(self):
        return _os.environ.get("APPROVAL_DB_PATH") or _os.path.expanduser("~/.orchestra/state/tasks.db")

    def _msg_db(self):
        return msg_db_path()

    @staticmethod
    def _last_line(r, prefix, what):
        out = (r.stdout or "").strip().splitlines()
        if r.returncode != 0 or not out or not out[-1].startswith(prefix):
            raise RuntimeError(f"{what} failed rc={r.returncode}: {(r.stderr or '')[-300:]}")
        return out[-1]

    def send(self, effect):
        if effect.kind == "task":
            return self._send_task(effect)
        if effect.kind == "dispatch":
            return self._send_dispatch(effect)
        if effect.kind == "results":
            p = effect.payload
            return self._message(effect, p["to"], "reply", p["subject"], p["body"])
        if effect.kind == "duenote":
            return self._send_duenote(effect)
        if effect.kind == "patch":
            return self._send_patch(effect)
        if effect.kind == "qack":
            qid = effect.payload["questionnaire"]
            r = _run([_sys.executable, self.cli, "qack", "--from", AUTHOR, qid], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(f"approval.py qack failed rc={r.returncode}: {(r.stderr or '')[-300:]}")
            return f"qack-{qid}"
        if effect.kind == "retire":
            qid = effect.payload["questionnaire"]
            r = _run([_sys.executable, self.cli, "retire", "--id", qid, "--from", AUTHOR,
                      "--reason", effect.payload["reason"]], capture_output=True, text=True, timeout=60)
            self._last_line(r, qid, "approval.py retire")
            return f"retire-{qid}"
        if effect.kind != "card":
            raise NotImplementedError(f"live adapter: {effect.kind} not wired yet")
        p = effect.payload
        with _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            _json.dump(p["questions"], f)
            qfile = f.name
        try:
            r = _run([_sys.executable, self.cli, "questionnaire", "--from", AUTHOR,
                                 "--title", p["title"], "--summary", p["summary"], "--feature", "post_call",
                                 "--op-key", effect.effect_id, "--questions-json", "@" + qfile],
                                capture_output=True, text=True, timeout=120)
        finally:
            _os.unlink(qfile)
        return self._last_line(r, "qnr_", "approval.py questionnaire")

    def _send_task(self, effect):
        """One human task per commitment (feature=commitment, op_key = the cmt_ id, H1 contract)."""
        p, it = effect.payload, effect.payload["item"]
        if p["action"] == "track":
            task = f"Chase {it['counterpart']}: {it['text']}"
            due_ts = p["chase_date"]
            due_source = (f"chase: their due {it['due_ts']} + 1bd" if it.get("due_ts") else "chase default: call + 2bd")
            said = "They said"
        else:
            task, due_ts, due_source, said = it["text"], it.get("due_ts"), it.get("due_source"), "You said"
        summary = "\n".join([task, f"Who: {it['counterpart']} · Due: {due_source or 'none stated'}",
                             f'{said}: "{it["quote"]}"', f"From: {it.get('call_title', '')} · {it['citation']}"])
        argv = [_sys.executable, self.cli, "human-task", "--from", AUTHOR, "--task", task, "--op-key", p["cmt"],
                "--feature", "commitment", "--blocks", it["counterpart"], "--summary", summary]
        if due_ts:
            argv += ["--evidence", _json.dumps({"due_ts": due_ts, "due_source": due_source})]
        r = _run(argv, capture_output=True, text=True, timeout=120)
        return self._last_line(r, "apr_", "approval.py human-task")

    def _message(self, effect, to, msg_type, subject, body):
        """One msg_store send, subject tagged with the effect id so find() can see it (design §1)."""
        with _tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(body)
            bfile = f.name
        try:
            r = _run([_sys.executable, self.msg_cli, "send", "--from", AUTHOR, "--to", to,
                                 "--type", msg_type, "--priority", "high",
                                 "--subject", f"[postcall {effect.effect_id}] {subject}",
                                 "--body-file", bfile], capture_output=True, text=True, timeout=120)
        finally:
            _os.unlink(bfile)
        try:
            out = _json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            out = {}
        if r.returncode != 0 or not out.get("sent"):
            raise RuntimeError(f"msg_store send failed rc={r.returncode}: {(r.stderr or r.stdout or '')[-300:]}")
        return out["id"]

    def _send_dispatch(self, effect):
        """The approved agent work, to the frozen seat, tagged with its dispatch id (design §6)."""
        p, it = effect.payload, effect.payload["item"]
        lost = p.get("rerouted_from")
        routing = ([f"Seat {lost} is not live in the registry: please pick a seat and route this work."] if lost
                   else ["No client seat resolved for this work: please pick a seat and route it; sending stays "
                         "with Shaw."] if p.get("route") else [])
        body = "\n".join(routing + [
            f"Approved by Shaw on card {p['decision_src']} (check: approval.py get <card id>).",
            f"Work: {p['agent_work']}", f"For: {it['text']} · with {it['counterpart']} · due {it.get('due_source')}",
            f'Quote: "{it["quote"]}" ({it["citation"]})',
            "Scope: drafts, research or internal artefacts only; never send anything as Shaw.",
            f"Idempotency key: {effect.effect_id}. If you already have it, reply with the existing result.",
            "Later due changes arrive as duenote:<cmt>:s<seq>; apply only a higher seq. Reply to: postcall-dev."])
        subject = (f"seat {lost} not live, please route: {p['agent_work']}" if lost
                   else f"please route: {p['agent_work']}" if p.get("route") else p["agent_work"])
        return self._message(effect, p["seat"], "task", subject, body)

    def _send_duenote(self, effect):
        """A due change for assigned work, to the seat the dispatch went to (design §5 table). The seat
        applies it only if its seq is newer than any it has seen for that dispatch."""
        p = effect.payload
        body = "\n".join([
            f"Due change for {p['dispatch']} (seq {p['seq']}): now due {p['due_ts']} ({p.get('due_source')}).",
            f"Apply only if seq {p['seq']} is newer than any due change you have seen for {p['dispatch']}.",
            f"Idempotency key: {effect.effect_id}. Reply to: postcall-dev."])
        return self._message(effect, p["to"], "task", f"Due changed for {p['dispatch']}: {p['due_ts']}", body)

    def _task_row(self, cmt):
        return self._ro(self._db(), "SELECT id FROM approval_requests WHERE from_agent=? AND op_key=? "
                                    "ORDER BY created_at LIMIT 1", (AUTHOR, cmt))

    @staticmethod
    def _patch_evidence(p):
        return {"due_ts": p["due_ts"], "due_source": p.get("due_source")}

    def _send_patch(self, effect):
        """Move a pending human task's due date: its evidence and the `Due:` part of its summary
        (design §5). Gated behind POSTCALL_RESCHEDULE_PATCH until the guarded-UPDATE fix lands."""
        from .ledger import _patch_enabled
        if not _patch_enabled():
            raise RuntimeError("live patch refused: POSTCALL_RESCHEDULE_PATCH is off (design §5, §7 step 6)")
        p = effect.payload
        rid = self._task_row(p["cmt"])
        if rid is None:
            raise RuntimeError(f"live patch: no human task with op_key {p['cmt']}")
        evidence = self._patch_evidence(p)
        argv = [_sys.executable, self.cli, "patch", "--id", rid, "--from", AUTHOR,
                "--evidence", _json.dumps(evidence)]
        summary = self._ro(self._db(), "SELECT summary FROM approval_requests WHERE id=?", (rid,))
        if summary:
            argv += ["--summary", _re.sub(r"^(Who: .*? · Due: ).*$",
                                          lambda m: m.group(1) + (evidence["due_source"] or "none stated"),
                                          summary, count=1, flags=_re.M)]
        r = _run(argv, capture_output=True, text=True, timeout=120)
        return self._last_line(r, "apr_", "approval.py patch")

    def card_answers(self, qid):
        """(status, {n: {option_n, final}}) for a filed questionnaire, read-only."""
        status = self._ro(self._db(), "SELECT status FROM questionnaires WHERE id=?", (qid,))
        if status is None:
            return "missing", {}
        with _sqlite3.connect(f"file:{self._db()}?mode=ro", uri=True) as c:
            rows = c.execute("SELECT n, option_n, state FROM questionnaire_answers WHERE qnr_id=?", (qid,)).fetchall()
        answers = {int(n): {"option_n": int(opt) if opt and str(opt).isdigit() else None, "final": state == "final"}
                   for n, opt, state in rows}
        return status, answers

    def _ro(self, path, sql, args):
        if not _os.path.exists(path):
            return None
        with _sqlite3.connect(f"file:{path}?mode=ro", uri=True) as c:
            try:
                row = c.execute(sql, args).fetchone()
            except _sqlite3.OperationalError:
                return None
        return row[0] if row else None

    def find(self, effect):
        if effect.kind == "card":
            return self._ro(self._db(), "SELECT id FROM questionnaires WHERE from_agent=? AND op_key=? "
                                        "ORDER BY created_at LIMIT 1", (AUTHOR, effect.effect_id))
        if effect.kind == "task":
            return self._task_row(effect.payload["cmt"])
        if effect.kind == "qack":
            qid = effect.payload["questionnaire"]
            st = self._ro(self._db(), "SELECT status FROM questionnaires WHERE id=?", (qid,))
            return f"qack-{qid}" if st == "resumed" else None
        if effect.kind == "retire":
            qid = effect.payload["questionnaire"]
            st = self._ro(self._db(), "SELECT status FROM questionnaires WHERE id=?", (qid,))
            return f"retire-{qid}" if st == "retired" else None
        if effect.kind == "patch":
            # Reconciliation is a read of the row's evidence: confirmed only once it says this date.
            rid = self._task_row(effect.payload["cmt"])
            raw = self._ro(self._db(), "SELECT evidence FROM approval_requests WHERE id=?", (rid,)) if rid else None
            try:
                return rid if raw and _json.loads(raw) == self._patch_evidence(effect.payload) else None
            except ValueError:
                return None
        if effect.kind in ("dispatch", "results", "duenote"):
            return self._ro(self._msg_db(), "SELECT id FROM messages WHERE from_agent=? AND subject LIKE ? "
                                            "ORDER BY created_at LIMIT 1", (AUTHOR, f"[postcall {effect.effect_id}]%"))
        return None


def adapter_for(which):
    if which == "live":
        return Live()
    if which == "shadow":
        return Shadow(_os.path.join(state_dir(), "shadow", "shadow.jsonl"))
    if which == "test":
        return Recorder()
    raise ValueError(which)
