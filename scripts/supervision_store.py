"""supervision_store.py — supervision levels, roles, assignments, standing grants and gate holds (attention part 2a).

Six additive tables in the ledger DB (state/tasks.db), created ONLY when the migration `m20261104_supervision` is
armed (approval_schema.armed: env APPROVAL_DDL_ARMED or the sentinel ~/runtime/APPROVAL_DDL_ARMED_<id>). Unarmed,
every read is empty (= no levels, roles or grants = Let them run) and every write raises SupervisionNotArmed, which
the gateway maps to 409 not_armed and the CLI to exit 3.

  supervision_settings  (person, agent) -> level / high_risk / hold_wait_s; agent '' is the person's default.
                        Agent ids are stored with their -genN suffix stripped. NULL fields are "not overridden".
  supervision_roles     permission templates; starter rows carry a `starter` key (hands_off | trusted_builder |
                        supervised) so a renamed starter is still recognised; `touched` = edited by the person.
  role_assignments      (person, agent|project, target) -> role. A project always keeps a role.
  standing_grants       allow/deny rows at session | agent | role | project | fleet scope, optionally narrowed by
                        action, folder (component containment) and recipient. Card grants are idempotent by
                        grant_key. Deny rows are immutable (narrowing a deny would widen what is allowed).
                        A session grant may name its agent (`agent`: display and member scoping only; the
                        session id alone decides what the grant reaches).
  supervision_meta      'gen' (bumped in the same transaction as every settings / role / assignment change; gate.json
                        carries it) and 'starters_seeded:<person>' (the starters are seeded once, ever).
  gate_holds            one row per held tool call. Every state change is a compare-and-set (transition_hold); the
                        caller acts only on True. Nothing leaves allowed / denied / released / withdrawn.

Seeds are max autonomy ("nothing changes"): the person's default is Let them run, every project folder gets the
Hands-off role, and no agent is assigned a role.

Writes: one short BEGIN IMMEDIATE transaction each; busy_timeout 5 s plus 3 bounded retries, then SupervisionBusy.
Timestamps are UTC ISO strings to the second with a Z suffix, so they compare correctly as text.

CLI:  supervision_store.py migrate [--seed]
      (--seed: the install's person default + starters + a Hands-off default for every registry project folder)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_config  # noqa: E402
import approval_schema  # noqa: E402

MIGRATION_ID = "m20261104_supervision"
_BUSY_MS = 5000
_BACKOFF_S = (0.1, 0.3, 0.9)
_pending_said = False

LEVELS = ("watch_closely", "check_in", "let_them_run")
CATEGORIES = ("outside_world", "destructive", "outside_folder", "install")
SCOPES = ("session", "agent", "role", "project", "fleet")
EFFECTS = ("allow", "deny")
GRANT_STATES = ("pending", "active", "revoked", "declined")
GRANT_SOURCES = ("card", "settings", "drawer", "chat", "seed", "role_copy")
SETTING_SOURCES = ("seed", "person")
TARGET_KINDS = ("agent", "project")
HOLD_STATES = ("filing", "waiting", "open", "allowed", "denied", "released", "withdrawn")
HOLD_FINAL = ("allowed", "denied", "released", "withdrawn")
HOLD_DECISIONS = ("allow_once", "allow_session", "allow_agent", "allow_fleet", "deny", "covered", "gate_off")
HOLD_FIELDS = ("card_id", "decision", "hook_seen_at", "decided_at", "retry_until", "consumed_at", "notified_at",
               "hits", "level")
MAX_WAIT_S = 540
NARROW_KEYS = ("action", "folder", "recipient")

STARTERS = (
    # (starter key, name, description, level, high_risk, role-scoped grants [(category, action)])
    ("hands_off", "Hands-off", "Agents run without stopping for you.", "let_them_run", [], []),
    ("trusted_builder", "Trusted builder", "Checks in before anything destructive or outside this server.",
     "check_in", ["destructive", "outside_world"], [("install", "npm"), ("install", "pip")]),
    ("supervised", "Supervised", "Checks in before every risky action.", "watch_closely", [], []),
)

_GEN_SUFFIX = re.compile(r"-gen\d+$")          # notify_policy / approval.py _canon

DDL = """
CREATE TABLE IF NOT EXISTS supervision_settings (
  person      TEXT NOT NULL,
  agent       TEXT NOT NULL DEFAULT '',
  level       TEXT,
  high_risk   TEXT,
  hold_wait_s INTEGER,
  source      TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  updated_by  TEXT NOT NULL,
  PRIMARY KEY (person, agent)
);

CREATE TABLE IF NOT EXISTS supervision_roles (
  id          TEXT PRIMARY KEY,
  person      TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT,
  level       TEXT NOT NULL,
  high_risk   TEXT NOT NULL DEFAULT '[]',
  hold_wait_s INTEGER NOT NULL DEFAULT 0,
  starter     TEXT,
  touched     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
  deleted_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_role_name ON supervision_roles(person, name) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS role_assignments (
  person      TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target      TEXT NOT NULL,
  role_id     TEXT NOT NULL,
  source      TEXT NOT NULL,
  updated_at  TEXT NOT NULL, updated_by TEXT NOT NULL,
  PRIMARY KEY (person, target_kind, target)
);

CREATE TABLE IF NOT EXISTS standing_grants (
  id               TEXT PRIMARY KEY,
  person           TEXT NOT NULL,
  scope            TEXT NOT NULL,
  scope_ref        TEXT,
  category         TEXT NOT NULL,
  narrow_action    TEXT,
  narrow_folder    TEXT,
  narrow_recipient TEXT,
  effect           TEXT NOT NULL DEFAULT 'allow',
  state            TEXT NOT NULL,
  source           TEXT NOT NULL,
  source_card      TEXT,
  said             TEXT,
  proposed_by      TEXT,
  created_at       TEXT NOT NULL,
  created_by       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  revoked_at       TEXT,
  revoked_by       TEXT,
  grant_key        TEXT,
  agent            TEXT
);
CREATE INDEX IF NOT EXISTS idx_grants_lookup ON standing_grants(person, state, category);
CREATE UNIQUE INDEX IF NOT EXISTS uq_grants_key ON standing_grants(grant_key) WHERE grant_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS supervision_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS gate_holds (
  id            TEXT PRIMARY KEY,
  card_id       TEXT,
  agent         TEXT NOT NULL,
  session_id    TEXT,
  tool_use_id   TEXT,
  tool          TEXT NOT NULL,
  fingerprint   TEXT NOT NULL,
  hits          TEXT NOT NULL,
  level         TEXT NOT NULL,
  state         TEXT NOT NULL,
  decision      TEXT,
  created_at    TEXT NOT NULL,
  hook_seen_at  TEXT,
  decided_at    TEXT,
  retry_until   TEXT,
  consumed_at   TEXT,
  notified_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_holds_agent_fp ON gate_holds(agent, fingerprint, state);
CREATE INDEX IF NOT EXISTS idx_holds_card ON gate_holds(card_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_holds_call ON gate_holds(session_id, tool_use_id) WHERE tool_use_id IS NOT NULL;
"""

_TABLES = ("supervision_settings", "supervision_roles", "role_assignments", "standing_grants", "supervision_meta",
           "gate_holds")


class SupervisionNotArmed(RuntimeError):
    """A write while m20261104_supervision is unarmed (gateway 409 not_armed, CLI exit 3)."""


class SupervisionBusy(RuntimeError):
    """The ledger stayed locked through every retry."""


class Invalid(ValueError):
    """A request the store refuses (gateway 400); str(e) is the machine code (name_taken, widen_not_allowed, …)."""


class Conflict(RuntimeError):
    """A request that conflicts with current state (gateway 409): role_in_use, last_role, grant_closed."""


class NotFound(LookupError):
    """No such role / grant (gateway 404)."""


_UNSET = object()


# ---- helpers ------------------------------------------------------------------------------------------------------

def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(now=None) -> str:
    """UTC, second precision, Z suffix. Accepts None (now), a datetime or an ISO string."""
    if now is None:
        return _fmt(datetime.now(timezone.utc))
    if isinstance(now, datetime):
        return _fmt(now if now.tzinfo else now.replace(tzinfo=timezone.utc))
    return _fmt(_parse(now))


def _gen_id(prefix: str) -> str:
    """<prefix>_<8 hex>_<ms mod 1e8> (approval_schema._gen_id's shape)."""
    return f"{prefix}_{secrets.token_hex(4)}_{int(time.time() * 1000) % 10**8}"


def _is_busy(e: Exception) -> bool:
    s = str(e).lower()
    return isinstance(e, sqlite3.OperationalError) and ("locked" in s or "busy" in s)


def base_agent(agent: str) -> str:
    """The agent id with its -genN suffix stripped (rotated names share one row)."""
    return _GEN_SUFFIX.sub("", agent or "")


def _inside_local(p: str, d: str) -> bool:
    """p is d or under d, by path components (/ab is not inside /a). Both must be absolute."""
    if not p or not d or not os.path.isabs(p) or not os.path.isabs(d):
        return False
    p, d = os.path.normpath(p), os.path.normpath(d)
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:
        return False


try:  # the classifier owns the containment rule (Task 2); fall back to the same component-wise check until then
    from gate_classify import inside  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    inside = _inside_local


def project_key(cwd: str | None, data_dir: str) -> str | None:
    """The project key for a working folder (review 17): a cwd under <data>/projects/<name>/… -> the realpath of
    <data>/projects/<name>; any other cwd -> its own realpath. None for an empty cwd."""
    if not cwd:
        return None
    real = os.path.realpath(cwd)
    root = os.path.realpath(os.path.join(data_dir, "projects"))
    if _inside_local(real, root) and real != root:
        name = os.path.relpath(real, root).split(os.sep)[0]
        return os.path.join(root, name)
    return real


def registry_project_keys(data_dir: str, registry: dict | None = None) -> list[str]:
    """Every distinct project key of the agents in <data>/registry.json (or the given registry dict)."""
    if registry is None:
        try:
            with open(os.path.join(data_dir, "registry.json")) as fh:
                registry = json.load(fh)
        except (OSError, ValueError):
            registry = {}
    agents = (registry or {}).get("agents") if isinstance(registry, dict) else None
    out = []
    for row in (agents or {}).values():
        cwd = row.get("cwd") if isinstance(row, dict) else None
        k = project_key(cwd, data_dir) if isinstance(cwd, str) else None
        if k and k not in out:
            out.append(k)
    return out


def _v_level(level, allow_none=True):
    if level is None and allow_none:
        return None
    if level not in LEVELS:
        raise Invalid("bad_level")
    return level


def _v_high_risk(hr, allow_none=True):
    if hr is None and allow_none:
        return None
    if not isinstance(hr, (list, tuple)) or any(c not in CATEGORIES for c in hr):
        raise Invalid("bad_high_risk")
    return json.dumps(list(dict.fromkeys(hr)))


def _v_wait(w, allow_none=True):
    if w is None and allow_none:
        return None
    if isinstance(w, bool) or not isinstance(w, int) or not 0 <= w <= MAX_WAIT_S:
        raise Invalid("bad_hold_wait_s")
    return w


def _v_narrow(narrow: dict | None) -> dict:
    narrow = dict(narrow or {})
    if set(narrow) - set(NARROW_KEYS):
        raise Invalid("bad_narrow")
    out = {}
    for k in NARROW_KEYS:
        v = narrow.get(k)
        if v is None or v == "":
            out[k] = None
            continue
        if not isinstance(v, str):
            raise Invalid("bad_narrow")
        if k == "folder":
            if not os.path.isabs(v):
                raise Invalid("bad_folder")
            v = os.path.realpath(v)          # the real path, as gate_classify records a hit's folder (symlinks)
        elif k == "recipient":
            v = v.strip().lower()
        out[k] = v
    return out


def grant_key(source_card, scope, scope_ref, category, action, folder, recipient) -> str:
    raw = "|".join("" if x is None else str(x) for x in (source_card, scope, scope_ref, category, action, folder,
                                                          recipient))
    return hashlib.sha256(raw.encode()).hexdigest()


# ---- the store ----------------------------------------------------------------------------------------------------

class SupervisionStore:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or approval_config.db_path())
        approval_config.refuse_live_db_under_pytest(self.db_path)   # the same fence as ApprovalStore
        self.armed = None

    # ---- plumbing ----
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=_BUSY_MS / 1000, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute(f"PRAGMA busy_timeout={int(_BUSY_MS)}")
        return c

    def _is_armed(self) -> bool:
        if self.armed is None:
            self.migrate()
        return bool(self.armed)

    def _write(self, fn):
        """Run fn(c) -> (result, changed) in one BEGIN IMMEDIATE transaction; bump gen when changed."""
        if not self._is_armed():
            raise SupervisionNotArmed(
                f"supervision store not armed ({MIGRATION_ID}); arm via APPROVAL_DDL_ARMED={MIGRATION_ID} "
                f"or the sentinel ~/runtime/APPROVAL_DDL_ARMED_{MIGRATION_ID}")
        last = None
        for attempt in range(len(_BACKOFF_S) + 1):
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                result, changed = fn(c)
                if changed:
                    self._bump(c)
                c.execute("COMMIT")
                return result
            except sqlite3.OperationalError as e:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if not _is_busy(e):
                    raise
                last = e
            except BaseException:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                c.close()
            if attempt < len(_BACKOFF_S):
                time.sleep(_BACKOFF_S[attempt])
        raise SupervisionBusy(f"the ledger is busy, run it again ({last})")

    def _read(self, fn, empty):
        if not self._is_armed():
            return empty
        c = self._conn()
        try:
            return fn(c)
        finally:
            c.close()

    @staticmethod
    def _bump(c):
        c.execute("INSERT INTO supervision_meta(key, value) VALUES('gen', '1') "
                  "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    # ---- schema ----
    def migrate(self):
        """Create the tables when armed; otherwise say PENDING once per process and stay inert."""
        global _pending_said
        if not approval_schema.armed(MIGRATION_ID):
            self.armed = False
            if not _pending_said:
                _pending_said = True
                print(f"[supervision_store] DDL PENDING (unarmed): {MIGRATION_ID} "
                      f"tables={','.join(_TABLES)} — arm via APPROVAL_DDL_ARMED={MIGRATION_ID} "
                      f"or sentinel ~/runtime/APPROVAL_DDL_ARMED_{MIGRATION_ID}", file=sys.stderr)
            return []
        last = None
        for attempt in range(len(_BACKOFF_S) + 1):
            c = self._conn()
            try:
                before = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                c.executescript(DDL)
                cols = {r[1] for r in c.execute("PRAGMA table_info(standing_grants)")}
                if "agent" not in cols:                  # a table made before the column existed
                    c.execute("ALTER TABLE standing_grants ADD COLUMN agent TEXT")
                after = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                break
            except sqlite3.OperationalError as e:
                if not _is_busy(e):
                    raise
                last = e
            finally:
                c.close()
            if attempt < len(_BACKOFF_S):
                time.sleep(_BACKOFF_S[attempt])
        else:
            raise SupervisionBusy(f"the ledger is busy, run it again ({last})")
        self.armed = True
        return sorted(t for t in after - before if t in _TABLES)

    # ---- meta ----
    def gen(self) -> int:
        def f(c):
            r = c.execute("SELECT value FROM supervision_meta WHERE key='gen'").fetchone()
            return int(r["value"]) if r else 0
        try:
            return self._read(f, 0)
        except sqlite3.Error:
            return 0

    def meta(self, key):
        return self._read(lambda c: (c.execute("SELECT value FROM supervision_meta WHERE key=?", [key])
                                     .fetchone() or {"value": None})["value"], None)

    # ---- settings ----
    def person_rows(self, person) -> dict:
        """{agent: raw row}; agent '' is the person's default. high_risk stays a JSON string (or NULL)."""
        return self._read(lambda c: {r["agent"]: dict(r) for r in c.execute(
            "SELECT * FROM supervision_settings WHERE person=?", [person])}, {})

    def _upsert_setting(self, person, agent, *, by, source, level, high_risk, hold_wait_s, now, drop_empty):
        if source not in SETTING_SOURCES:
            raise Invalid("bad_source")
        vals = {}
        if level is not _UNSET:
            vals["level"] = _v_level(level)
        if high_risk is not _UNSET:
            vals["high_risk"] = _v_high_risk(high_risk)
        if hold_wait_s is not _UNSET:
            vals["hold_wait_s"] = _v_wait(hold_wait_s)
        ts = _iso(now)

        def f(c):
            prev = c.execute("SELECT * FROM supervision_settings WHERE person=? AND agent=?",
                             [person, agent]).fetchone()
            row = dict(prev) if prev else {"level": None, "high_risk": None, "hold_wait_s": None}
            row.update(vals)
            if drop_empty and all(row[k] is None for k in ("level", "high_risk", "hold_wait_s")):
                n = c.execute("DELETE FROM supervision_settings WHERE person=? AND agent=?", [person, agent]).rowcount
                return None, n > 0
            c.execute("INSERT INTO supervision_settings(person, agent, level, high_risk, hold_wait_s, source, "
                      "updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(person, agent) DO UPDATE SET "
                      "level=excluded.level, high_risk=excluded.high_risk, hold_wait_s=excluded.hold_wait_s, "
                      "source=excluded.source, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                      [person, agent, row["level"], row["high_risk"], row["hold_wait_s"], source, ts, by])
            return dict(c.execute("SELECT * FROM supervision_settings WHERE person=? AND agent=?",
                                  [person, agent]).fetchone()), True
        return self._write(f)

    def set_default(self, person, *, by, level=_UNSET, high_risk=_UNSET, hold_wait_s=_UNSET, source="person",
                    now=None) -> dict:
        """Set the person's default (only the fields given). Returns the row."""
        return self._upsert_setting(person, "", by=by, source=source, level=level, high_risk=high_risk,
                                    hold_wait_s=hold_wait_s, now=now, drop_empty=False)

    def set_agent(self, person, agent, *, by, level=_UNSET, high_risk=_UNSET, hold_wait_s=_UNSET, source="person",
                  now=None) -> dict | None:
        """Override fields for one agent (-genN stripped); None clears a field. A row with nothing overridden is
        deleted (returns None)."""
        a = base_agent(agent)
        if not a:
            raise Invalid("bad_agent")
        return self._upsert_setting(person, a, by=by, source=source, level=level, high_risk=high_risk,
                                    hold_wait_s=hold_wait_s, now=now, drop_empty=True)

    # ---- roles ----
    def roles(self, person, include_deleted=False) -> list[dict]:
        sql = "SELECT * FROM supervision_roles WHERE person=?" + ("" if include_deleted else " AND deleted_at IS NULL")
        return self._read(lambda c: [dict(r) for r in c.execute(sql + " ORDER BY created_at, id", [person])], [])

    def role(self, role_id) -> dict | None:
        return self._read(lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM supervision_roles WHERE id=?", [role_id]).fetchone()), None)

    @staticmethod
    def _live_role(c, person, role_id):
        r = c.execute("SELECT * FROM supervision_roles WHERE id=? AND person=? AND deleted_at IS NULL",
                      [role_id, person]).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _insert_role(c, person, *, name, level, high_risk, hold_wait_s, description, starter, by, ts):
        if not isinstance(name, str) or not name.strip():
            raise Invalid("bad_name")
        name = name.strip()
        if c.execute("SELECT 1 FROM supervision_roles WHERE person=? AND name=? AND deleted_at IS NULL",
                     [person, name]).fetchone():
            raise Invalid("name_taken")
        rid = _gen_id("rol")
        c.execute("INSERT INTO supervision_roles(id, person, name, description, level, high_risk, hold_wait_s, "
                  "starter, touched, created_at, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
                  [rid, person, name, description, _v_level(level, False), _v_high_risk(list(high_risk), False),
                   _v_wait(hold_wait_s, False), starter, ts, ts, by])
        return rid

    def create_role(self, person, *, name, level, by, high_risk=(), hold_wait_s=0, description=None,
                    now=None) -> dict:
        ts = _iso(now)

        def f(c):
            rid = self._insert_role(c, person, name=name, level=level, high_risk=high_risk, hold_wait_s=hold_wait_s,
                                    description=description, starter=None, by=by, ts=ts)
            return self._live_role(c, person, rid), True
        return self._write(f)

    def update_role(self, person, role_id, *, by, name=_UNSET, description=_UNSET, level=_UNSET,
                    high_risk=_UNSET, hold_wait_s=_UNSET, now=None) -> dict:
        """Edit a role (only the fields given); a starter edited this way is never re-seeded (touched=1)."""
        sets = {}
        if name is not _UNSET:
            if not isinstance(name, str) or not name.strip():
                raise Invalid("bad_name")
            sets["name"] = name.strip()
        if description is not _UNSET:
            sets["description"] = description
        if level is not _UNSET:
            sets["level"] = _v_level(level, False)
        if high_risk is not _UNSET:
            sets["high_risk"] = _v_high_risk(high_risk, False)
        if hold_wait_s is not _UNSET:
            sets["hold_wait_s"] = _v_wait(hold_wait_s, False)
        ts = _iso(now)

        def f(c):
            if not self._live_role(c, person, role_id):
                raise NotFound("role")
            if "name" in sets and c.execute(
                    "SELECT 1 FROM supervision_roles WHERE person=? AND name=? AND id<>? AND deleted_at IS NULL",
                    [person, sets["name"], role_id]).fetchone():
                raise Invalid("name_taken")
            cols = dict(sets, touched=1, updated_at=ts, updated_by=by)
            c.execute(f"UPDATE supervision_roles SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                      list(cols.values()) + [role_id])
            return self._live_role(c, person, role_id), True
        return self._write(f)

    def duplicate_role(self, person, role_id, *, by, name=None, now=None) -> dict:
        """Copy a role and its live role-scoped grants (source='role_copy'). The copy is user-made (starter NULL)."""
        ts = _iso(now)

        def f(c):
            src = self._live_role(c, person, role_id)
            if not src:
                raise NotFound("role")
            rid = self._insert_role(c, person, name=name or f"{src['name']} (copy)", level=src["level"],
                                    high_risk=json.loads(src["high_risk"] or "[]"), hold_wait_s=src["hold_wait_s"],
                                    description=src["description"], starter=None, by=by, ts=ts)
            for g in c.execute("SELECT * FROM standing_grants WHERE person=? AND scope='role' AND scope_ref=? "
                               "AND state IN ('active','pending')", [person, role_id]).fetchall():
                self._insert_grant(c, person, scope="role", scope_ref=rid, category=g["category"],
                                   narrow={"action": g["narrow_action"], "folder": g["narrow_folder"],
                                           "recipient": g["narrow_recipient"]},
                                   effect=g["effect"], state=g["state"], source="role_copy", source_card=None,
                                   said=None, proposed_by=None, by=by, ts=ts)
            return self._live_role(c, person, rid), True
        return self._write(f)

    def delete_role(self, person, role_id, *, by, now=None) -> dict:
        """Soft delete. Conflict('role_in_use') while anything is assigned to it; Conflict('last_role') for the
        person's only role. Its role-scoped grants are revoked with it."""
        ts = _iso(now)

        def f(c):
            r = self._live_role(c, person, role_id)
            if not r:
                raise NotFound("role")
            if c.execute("SELECT 1 FROM role_assignments WHERE person=? AND role_id=?", [person, role_id]).fetchone():
                raise Conflict("role_in_use")
            n = c.execute("SELECT COUNT(*) FROM supervision_roles WHERE person=? AND deleted_at IS NULL",
                          [person]).fetchone()[0]
            if n <= 1:
                raise Conflict("last_role")
            c.execute("UPDATE supervision_roles SET deleted_at=?, updated_at=?, updated_by=? WHERE id=?",
                      [ts, ts, by, role_id])
            c.execute("UPDATE standing_grants SET state='revoked', revoked_at=?, revoked_by=?, updated_at=? "
                      "WHERE person=? AND scope='role' AND scope_ref=? AND state IN ('active','pending')",
                      [ts, by, ts, person, role_id])
            return dict(c.execute("SELECT * FROM supervision_roles WHERE id=?", [role_id]).fetchone()), True
        return self._write(f)

    # ---- assignments ----
    def assignments(self, person) -> dict:
        """{(target_kind, target): (role_id, source)}."""
        return self._read(lambda c: {(r["target_kind"], r["target"]): (r["role_id"], r["source"]) for r in c.execute(
            "SELECT * FROM role_assignments WHERE person=?", [person])}, {})

    def assign(self, person, target_kind, target, role_id, *, by, source="person", now=None):
        """Point an agent (-genN stripped) or a project key at a role. role_id None removes an agent's role; a
        project always keeps one (Invalid('project_needs_role'))."""
        if target_kind not in TARGET_KINDS:
            raise Invalid("bad_target_kind")
        if source not in SETTING_SOURCES:
            raise Invalid("bad_source")
        target = base_agent(target) if target_kind == "agent" else target
        if not isinstance(target, str) or not target:
            raise Invalid("bad_target")
        if role_id is None and target_kind == "project":
            raise Invalid("project_needs_role")
        ts = _iso(now)

        def f(c):
            if role_id is None:
                n = c.execute("DELETE FROM role_assignments WHERE person=? AND target_kind=? AND target=?",
                              [person, target_kind, target]).rowcount
                return None, n > 0
            if not self._live_role(c, person, role_id):
                raise Invalid("unknown_role")
            c.execute("INSERT INTO role_assignments(person, target_kind, target, role_id, source, updated_at, "
                      "updated_by) VALUES (?,?,?,?,?,?,?) ON CONFLICT(person, target_kind, target) DO UPDATE SET "
                      "role_id=excluded.role_id, source=excluded.source, updated_at=excluded.updated_at, "
                      "updated_by=excluded.updated_by", [person, target_kind, target, role_id, source, ts, by])
            return (role_id, source), True
        return self._write(f)

    # ---- seeding ----
    def seed(self, person, projects=(), *, by="seed", now=None) -> dict:
        """Once per person (marker starters_seeded:<person>): the default row (Let them run, [], 0, source seed;
        never over a row the person already set) and the three starter roles with Trusted builder's grants. Then
        seed_projects. No agent is ever assigned. Returns {'starters': bool, 'projects': [keys added]}."""
        ts = _iso(now)
        marker = f"starters_seeded:{person}"

        def f(c):
            if c.execute("SELECT 1 FROM supervision_meta WHERE key=?", [marker]).fetchone():
                return False, False
            c.execute("INSERT OR IGNORE INTO supervision_settings(person, agent, level, high_risk, hold_wait_s, "
                      "source, updated_at, updated_by) VALUES (?, '', 'let_them_run', '[]', 0, 'seed', ?, ?)",
                      [person, ts, by])
            for key, name, desc, level, hr, grants in STARTERS:
                if c.execute("SELECT 1 FROM supervision_roles WHERE person=? AND starter=?", [person, key]).fetchone():
                    continue
                nm = name
                if c.execute("SELECT 1 FROM supervision_roles WHERE person=? AND name=? AND deleted_at IS NULL",
                             [person, nm]).fetchone():
                    nm = f"{name} (starter)"
                rid = self._insert_role(c, person, name=nm, level=level, high_risk=hr, hold_wait_s=0,
                                        description=desc, starter=key, by=by, ts=ts)
                for cat, action in grants:
                    self._insert_grant(c, person, scope="role", scope_ref=rid, category=cat,
                                       narrow={"action": action}, effect="allow", state="active", source="seed",
                                       source_card=None, said=None, proposed_by=None, by=by, ts=ts)
            c.execute("INSERT INTO supervision_meta(key, value) VALUES (?, ?)", [marker, ts])
            return True, True
        starters = self._write(f)
        added = self.seed_projects(person, projects, by=by, now=now) if projects else []
        return {"starters": starters, "projects": added}

    def seed_projects(self, person, projects, *, by="seed", now=None) -> list[str]:
        """Give every project key without an assignment the Hands-off starter (found by its starter key, so a
        rename keeps working). No-op when Hands-off was deleted. Returns the keys added."""
        keys = [p for p in dict.fromkeys(projects or []) if isinstance(p, str) and p]
        if not keys:
            return []
        ts = _iso(now)

        def f(c):
            r = c.execute("SELECT id FROM supervision_roles WHERE person=? AND starter='hands_off' "
                          "AND deleted_at IS NULL", [person]).fetchone()
            if not r:
                return [], False
            added = []
            for k in keys:
                n = c.execute("INSERT OR IGNORE INTO role_assignments(person, target_kind, target, role_id, source, "
                              "updated_at, updated_by) VALUES (?, 'project', ?, ?, 'seed', ?, ?)",
                              [person, k, r["id"], ts, by]).rowcount
                if n:
                    added.append(k)
            return added, bool(added)
        return self._write(f)

    # ---- grants ----
    @staticmethod
    def _insert_grant(c, person, *, scope, scope_ref, category, narrow, effect, state, source, source_card, said,
                      proposed_by, by, ts, agent=None):
        """Validate and insert; card grants are idempotent by grant_key (returns the existing id)."""
        if scope not in SCOPES:
            raise Invalid("bad_scope")
        if scope == "fleet":
            if scope_ref is not None:
                raise Invalid("bad_scope_ref")
        elif not isinstance(scope_ref, str) or not scope_ref:
            raise Invalid("bad_scope_ref")
        if scope == "agent":
            scope_ref = base_agent(scope_ref)
        if category not in CATEGORIES:
            raise Invalid("bad_category")
        if effect not in EFFECTS:
            raise Invalid("bad_effect")
        if state not in ("pending", "active"):
            raise Invalid("bad_state")
        if source not in GRANT_SOURCES:
            raise Invalid("bad_source")
        if agent is not None and (scope != "session" or not isinstance(agent, str) or not agent):
            raise Invalid("bad_agent")
        n = _v_narrow(narrow)
        key = None
        if source == "card":
            if not source_card:
                raise Invalid("card_grant_needs_card")
            key = grant_key(source_card, scope, scope_ref, category, n["action"], n["folder"], n["recipient"])
            r = c.execute("SELECT id FROM standing_grants WHERE grant_key=?", [key]).fetchone()
            if r:
                return r["id"], False
        gid = _gen_id("grt")
        c.execute("INSERT INTO standing_grants(id, person, scope, scope_ref, category, narrow_action, narrow_folder, "
                  "narrow_recipient, effect, state, source, source_card, said, proposed_by, created_at, created_by, "
                  "updated_at, grant_key, agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [gid, person, scope, scope_ref, category, n["action"], n["folder"], n["recipient"], effect, state,
                   source, source_card, said, proposed_by, ts, by, ts, key, agent])
        return gid, True

    def create_grant(self, person, *, scope, scope_ref, category, source, by, narrow=None, effect="allow",
                     state="active", source_card=None, said=None, proposed_by=None, agent=None, now=None) -> dict:
        """A new grant (state active, or pending for a chat proposal). A card grant replayed with the same
        (source_card, scope, scope_ref, category, narrowing) returns the existing row. `agent` names a session
        grant's agent (session scope only; -genN stripped)."""
        ts = _iso(now)

        def f(c):
            gid, _new = self._insert_grant(c, person, scope=scope, scope_ref=scope_ref, category=category,
                                           narrow=narrow, effect=effect, state=state, source=source,
                                           source_card=source_card, said=said, proposed_by=proposed_by, by=by, ts=ts,
                                           agent=base_agent(agent) if agent else None)
            return dict(c.execute("SELECT * FROM standing_grants WHERE id=?", [gid]).fetchone()), False
        return self._write(f)

    def grant(self, grant_id) -> dict | None:
        return self._read(lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM standing_grants WHERE id=?", [grant_id]).fetchone()), None)

    def grants(self, person, *, scope=None, scope_ref=_UNSET, states=("active", "pending"), category=None) -> list:
        """The person's grants, oldest first. Default states: active and pending (revoked/declined drop out)."""
        sql, args = "SELECT * FROM standing_grants WHERE person=?", [person]
        st = list(states or ())
        if st:
            sql += f" AND state IN ({','.join('?' for _ in st)})"
            args += st
        if scope is not None:
            sql += " AND scope=?"
            args.append(scope)
        if scope_ref is not _UNSET:
            if scope_ref is None:
                sql += " AND scope_ref IS NULL"
            else:
                sql += " AND scope_ref=?"
                args.append(scope_ref)
        if category is not None:
            sql += " AND category=?"
            args.append(category)
        return self._read(lambda c: [dict(r) for r in c.execute(sql + " ORDER BY created_at, id", args)], [])

    def _grant_state(self, grant_id, to_state, from_states, *, by, now, extra=None):
        ts = _iso(now)

        def f(c):
            r = c.execute("SELECT * FROM standing_grants WHERE id=?", [grant_id]).fetchone()
            if not r:
                raise NotFound("grant")
            if r["state"] == to_state:
                return dict(r), False
            if r["state"] not in from_states:
                raise Conflict("grant_closed")
            cols = {"state": to_state, "updated_at": ts}
            cols.update({k: (ts if v is _NOW else by if v is _BY else v) for k, v in (extra or {}).items()})
            c.execute(f"UPDATE standing_grants SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                      list(cols.values()) + [grant_id])
            return dict(c.execute("SELECT * FROM standing_grants WHERE id=?", [grant_id]).fetchone()), False
        return self._write(f)

    def activate_grant(self, grant_id, *, by, now=None) -> dict:
        """pending -> active (the person confirmed a chat grant). Idempotent; Conflict('grant_closed') otherwise."""
        return self._grant_state(grant_id, "active", ("pending",), by=by, now=now)

    def decline_grant(self, grant_id, *, by, now=None) -> dict:
        return self._grant_state(grant_id, "declined", ("pending",), by=by, now=now,
                                 extra={"revoked_at": _NOW, "revoked_by": _BY})

    def revoke_grant(self, grant_id, *, by, now=None) -> dict:
        return self._grant_state(grant_id, "revoked", ("pending", "active"), by=by, now=now,
                                 extra={"revoked_at": _NOW, "revoked_by": _BY})

    def narrow_grant(self, grant_id, narrow: dict, *, by, now=None) -> dict:
        """Narrow a live allow grant: a value set over an unset one, or a sub-folder of the current folder.
        Anything else raises Invalid('widen_not_allowed'); a deny raises Invalid('deny_immutable')."""
        n_in = dict(narrow or {})
        if set(n_in) - set(NARROW_KEYS):
            raise Invalid("bad_narrow")
        new = _v_narrow(n_in)
        ts = _iso(now)

        def f(c):
            r = c.execute("SELECT * FROM standing_grants WHERE id=?", [grant_id]).fetchone()
            if not r:
                raise NotFound("grant")
            if r["state"] not in ("active", "pending"):
                raise Conflict("grant_closed")
            if r["effect"] == "deny":
                raise Invalid("deny_immutable")
            cols = {}
            for k in n_in:
                cur, nv = r[f"narrow_{k}"], new[k]
                if nv == cur:
                    continue
                if nv is None:
                    raise Invalid("widen_not_allowed")
                if cur is not None and not (k == "folder" and inside(nv, cur)):
                    raise Invalid("widen_not_allowed")
                cols[f"narrow_{k}"] = nv
            if cols:
                cols["updated_at"] = ts
                c.execute(f"UPDATE standing_grants SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                          list(cols.values()) + [grant_id])
            return dict(c.execute("SELECT * FROM standing_grants WHERE id=?", [grant_id]).fetchone()), False
        return self._write(f)

    # ---- holds ----
    def create_hold(self, *, agent, tool, fingerprint, hits, level, session_id=None, tool_use_id=None, card_id=None,
                    state="filing", now=None) -> str:
        """Insert a hold (state 'filing' by default) and return its id. A second insert for the same
        (session_id, tool_use_id) returns the existing hold's id; re-read it with hold(id) to follow its state."""
        if state not in HOLD_STATES:
            raise Invalid("bad_state")
        ts = _iso(now)
        hits_s = hits if isinstance(hits, str) else json.dumps(list(hits or []))

        def f(c):
            if tool_use_id is not None:
                r = c.execute("SELECT id FROM gate_holds WHERE session_id IS ? AND tool_use_id=?",
                              [session_id, tool_use_id]).fetchone()
                if r:
                    return r["id"], False
            hid = _gen_id("gh")
            c.execute("INSERT INTO gate_holds(id, card_id, agent, session_id, tool_use_id, tool, fingerprint, hits, "
                      "level, state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      [hid, card_id, agent, session_id, tool_use_id, tool, fingerprint, hits_s, level, state, ts])
            return hid, False
        return self._write(f)

    def hold(self, hold_id) -> dict | None:
        """The raw row (hits is a JSON string)."""
        return self._read(lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM gate_holds WHERE id=?", [hold_id]).fetchone()), None)

    def hold_by_card(self, card_id) -> dict | None:
        return self._read(lambda c: (lambda r: dict(r) if r else None)(c.execute(
            "SELECT * FROM gate_holds WHERE card_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            [card_id]).fetchone()), None)

    def open_hold(self, agent, fingerprint, states=("open",)) -> dict | None:
        """The newest hold for (agent, fingerprint) in one of `states` (default open): a retry before the answer
        gets "still held on card <id>", never a second card."""
        st = list(states)
        return self._read(lambda c: (lambda r: dict(r) if r else None)(c.execute(
            f"SELECT * FROM gate_holds WHERE agent=? AND fingerprint=? AND state IN ({','.join('?' for _ in st)}) "
            "ORDER BY created_at DESC, id DESC LIMIT 1", [agent, fingerprint] + st).fetchone()), None)

    def open_holds(self, states=("open",)) -> list[dict]:
        st = list(states)
        return self._read(lambda c: [dict(r) for r in c.execute(
            f"SELECT * FROM gate_holds WHERE state IN ({','.join('?' for _ in st)}) ORDER BY created_at, id", st)],
            [])

    @staticmethod
    def _hold_cols(fields, ts):
        bad = set(fields) - set(HOLD_FIELDS) - {"state"}
        if bad:
            raise Invalid("bad_hold_field")
        cols = {}
        for k, v in fields.items():
            if k == "hits" and not isinstance(v, str) and v is not None:
                v = json.dumps(list(v))
            elif k.endswith("_at") or k == "retry_until":
                v = None if v is None else _iso(v)
            cols[k] = v
        return cols

    def transition_hold(self, hold_id, to_state, from_states, *, now=None, **fields) -> bool:
        """The compare-and-set every transition uses: UPDATE … SET state=to_state, <fields> WHERE id=? AND state IN
        from_states. True = this caller won; False = someone else did (re-read with hold() and follow it). Moving to
        allowed/denied/released/withdrawn sets decided_at (unless given)."""
        if to_state not in HOLD_STATES or not from_states or any(s not in HOLD_STATES for s in from_states):
            raise Invalid("bad_state")
        ts = _iso(now)
        cols = self._hold_cols(fields, ts)
        cols["state"] = to_state
        if to_state in HOLD_FINAL and "decided_at" not in cols:
            cols["decided_at"] = ts
        fs = list(from_states)

        def f(c):
            n = c.execute(f"UPDATE gate_holds SET {', '.join(k + '=?' for k in cols)} WHERE id=? AND state IN "
                          f"({','.join('?' for _ in fs)})", list(cols.values()) + [hold_id] + fs).rowcount
            return n == 1, False
        return self._write(f)

    def set_hold(self, hold_id, *, now=None, **fields) -> bool:
        """Unconditional field update (tests and simple bookkeeping). Production state changes use
        transition_hold."""
        if "state" in fields and fields["state"] not in HOLD_STATES:
            raise Invalid("bad_state")
        cols = self._hold_cols(fields, _iso(now))
        if not cols:
            return False

        def f(c):
            n = c.execute(f"UPDATE gate_holds SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                          list(cols.values()) + [hold_id]).rowcount
            return n == 1, False
        return self._write(f)

    def heartbeat(self, hold_id, now=None) -> bool:
        """A waiting hook's 5 s pulse (CAS on state='waiting'). False: the hold moved on; re-read and follow it."""
        ts = _iso(now)
        return self._write(lambda c: (c.execute(
            "UPDATE gate_holds SET hook_seen_at=? WHERE id=? AND state='waiting'", [ts, hold_id]).rowcount == 1,
            False))

    def claim_notify(self, hold_id, now=None) -> bool:
        """True exactly once per hold: the caller that wins messages the agent."""
        ts = _iso(now)
        return self._write(lambda c: (c.execute(
            "UPDATE gate_holds SET notified_at=? WHERE id=? AND notified_at IS NULL", [ts, hold_id]).rowcount == 1,
            False))

    def take_retry(self, agent, fingerprint, now=None) -> str | None:
        """Use the one-time pass an Allow left on an allowed hold (retry_until in the future, not yet consumed).
        Returns the hold id, or None. Single use: a CAS on consumed_at IS NULL."""
        ts = _iso(now)
        return self._write(lambda c: ((lambda r: r[0] if r else None)(c.execute(
            "UPDATE gate_holds SET consumed_at=? WHERE id=(SELECT id FROM gate_holds WHERE agent=? AND fingerprint=? "
            "AND state='allowed' AND retry_until>? AND consumed_at IS NULL ORDER BY created_at LIMIT 1) "
            "AND consumed_at IS NULL RETURNING id", [ts, agent, fingerprint, ts]).fetchone()), False))

    def sweep_stale(self, *, stale_s=30, now=None, now_offset_s=0) -> list[dict]:
        """waiting holds whose heartbeat (or, before the first one, created_at) is older than stale_s, and filing
        holds older than stale_s -> withdrawn. An open hold is never swept. Returns the withdrawn rows."""
        base = _parse(_iso(now)) + timedelta(seconds=now_offset_s)
        ts, cutoff = _fmt(base), _fmt(base - timedelta(seconds=stale_s))

        def f(c):
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM gate_holds WHERE (state='waiting' AND COALESCE(hook_seen_at, created_at) < ?) "
                "OR (state='filing' AND created_at < ?) ORDER BY created_at, id", [cutoff, cutoff])]
            out = []
            for hid in ids:
                n = c.execute("UPDATE gate_holds SET state='withdrawn', decided_at=? WHERE id=? "
                              "AND state IN ('waiting','filing')", [ts, hid]).rowcount
                if n:
                    out.append(dict(c.execute("SELECT * FROM gate_holds WHERE id=?", [hid]).fetchone()))
            return out, False
        return self._write(f)


_NOW = object()
_BY = object()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="supervision_store.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("migrate")
    pm.add_argument("--seed", action="store_true",
                    help="also seed the install's person (Let them run, starters, Hands-off per registry project)")
    args = p.parse_args(argv)
    st = SupervisionStore()
    made = st.migrate()
    if not st.armed:
        print(f"not armed: {MIGRATION_ID} (nothing created)")
        return 3
    print("tables created: " + (", ".join(made) if made else "none (already present)"))
    if args.seed:
        try:
            import notify_policy
            person, _ = notify_policy.install_user_and_zone()
        except Exception:  # noqa: BLE001 — a missing or broken config is the default operator
            person = "operator"
        data = str(approval_config.orchestra_dir())
        projects = registry_project_keys(data)
        res = st.seed(person, projects)
        print(f"seeded {person}: starters {'created' if res['starters'] else 'already seeded'}; "
              f"{len(res['projects'])} project default(s) added of {len(projects)} project folder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
