"""Seat names: a changeable display name for each agent, on top of its permanent id.

The id (the lineage root: "pm-demo") stays the address everywhere: registry, tmux session, mailbox,
parent links, files, URLs. A name is a label people and agents see ("Lead"); renaming never moves mail.

Rules:
- A name belongs to the LINEAGE, not to one generation, so a rename carries over to every later generation
  (a rotation or a fresh successor) with nothing to copy.
- Old names stay the agent's forever, as aliases: anything written with an old name still reaches it.
- Names are unique per PROJECT (seat_projects.py: the project of the first PM up reports_to): two projects can
  each have a "dev", one project can't have two. Within a project a name can't match another agent's current or
  old name, living or dead (a dead agent can be revived). Install-wide, a name can't match any agent's id or a
  built-in mailbox, and full names ("Demo · dev") stay unique because project labels are. Matching is on the
  name's KEY: letters and digits only, case-folded, so "PM Demo" and "pm-demo" are the same name.
- Each name records the project it was given in (its scope), so after a move its old full name stays an alias.

Stored in the identity DB (state/orchestra-registry.db), not registry.json: registry.json is a projection
the DB rewrites, so a field kept only there would be lost. The table is created by this module on first use
(CREATE TABLE IF NOT EXISTS), so nothing changes in a live DB until a name is read or set through here.
"""
import datetime
import json
import os
import re
import sqlite3
import sys
import unicodedata

if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.identity_store.orchestra_db import get_connection  # noqa: E402

NAMES_SCHEMA = """
CREATE TABLE IF NOT EXISTS seat_names (    -- every name an agent has had; one current per lineage
  root       TEXT NOT NULL,                -- the agent's permanent id (lineage root)
  scope      TEXT NOT NULL DEFAULT '',     -- the project (slug) it was named in; '' = no project
  name_key   TEXT NOT NULL,                -- the name's key (letters+digits, case-folded)
  name       TEXT NOT NULL,                -- as written
  is_current INTEGER NOT NULL DEFAULT 0,
  set_at     TEXT NOT NULL,
  set_by     TEXT,
  PRIMARY KEY (root, scope, name_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS seat_names_one_current ON seat_names(root) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS seat_names_by_key ON seat_names(name_key);
"""

MAX_LEN = 40
# Mailboxes that are not seats but receive mail by these ids: the assistant (named by the install's assistant
# name, services/arturo/assistant_name.py) and the operator (the person; scripts/away.py reads it). They are
# always valid addresses and their ids and names are never available to an agent.
BUILTIN_IDS = ("arturo", "operator")


def assistant_display_name():
    """The assistant's name on this install ("Arturo" until renamed), or None when it can't be read."""
    try:
        from services.arturo import assistant_name
        return assistant_name.name()
    except Exception:  # noqa: BLE001 - never fail a name check over the assistant's name
        return None
GEN_SUFFIX = re.compile(r"-(?:gen|g)\d+$")      # a generation's id ("pm-demo-g3", "-gen2") -> its lineage
_INNER = set("-_.'’")


class NameError_(ValueError):
    """A name that can't be used, with the reason a person can read."""


def base_id(agent_id: str) -> str:
    """A generation's id to its lineage root ("pm-demo-gen3" -> "pm-demo")."""
    return GEN_SUFFIX.sub("", str(agent_id or "").strip())


def name_key(text: str) -> str:
    """The name's identity: letters and digits only, case-folded and NFKC-normalised."""
    t = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return "".join(ch for ch in t if ch.isalnum())


def validate_name(raw) -> str:
    """The name tidied (trimmed, inner spaces collapsed); raises NameError_ with a plain reason."""
    if not isinstance(raw, str):
        raise NameError_("name must be text")
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not name:
        raise NameError_("name can't be empty")
    if len(name) > MAX_LEN:
        raise NameError_(f"name must be at most {MAX_LEN} characters")
    for i, ch in enumerate(name):
        if ch.isalnum() or ch == " ":
            continue
        if ch in _INNER and 0 < i < len(name) - 1 and name[i - 1] != " " and name[i + 1] != " ":
            continue
        raise NameError_("use letters, numbers and spaces (- _ . ' inside a word are fine)")
    if not any(ch.isalpha() for ch in name):
        raise NameError_("name needs at least one letter")
    return name


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ensure(conn):
    """Create the names table (no-op when it exists). Never inside a transaction: executescript commits first."""
    conn.executescript(NAMES_SCHEMA)


def _has_table(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seat_names'").fetchone())


def db_path_for(orchestra_dir) -> str:
    return os.path.join(str(orchestra_dir), "state", "orchestra-registry.db")


def known_ids(conn, orchestra_dir=None) -> set:
    """Every agent id that exists or ever existed: the DB's lineages and generations' roots, plus the
    registry and session files (which can hold a seat the DB has not caught up with). Lineage roots only."""
    ids = set()
    for (root,) in conn.execute("SELECT root FROM lineages"):
        ids.add(base_id(root))
    if orchestra_dir:
        for rel, pick in (("registry.json", lambda d: (d.get("agents") or {})),
                          (os.path.join("state", "agent-sessions.json"), lambda d: d)):
            try:
                d = json.loads(open(os.path.join(str(orchestra_dir), rel)).read())
                for k, v in (pick(d) or {}).items():
                    if k.startswith("_"):
                        continue
                    ids.add(base_id(k))
                    if isinstance(v, dict) and v.get("lineage_root"):
                        ids.add(base_id(v["lineage_root"]))
            except (OSError, ValueError, AttributeError):
                pass
    ids.discard("")
    return ids | set(BUILTIN_IDS)


def all_names(conn) -> dict:
    """{root: {"name": current or None, "scope": its project, "aliases": [old names, oldest first],
    "alias_rows": [(old name, project it was named in)]}} for every named agent."""
    out = {}
    if not _has_table(conn):
        return out
    for root, name, scope, cur in conn.execute("SELECT root, name, scope, is_current FROM seat_names ORDER BY set_at, rowid"):
        rec = out.setdefault(root, {"name": None, "scope": "", "aliases": [], "alias_rows": []})
        if cur:
            rec["name"], rec["scope"] = name, scope
        else:
            rec["aliases"].append(name)
            rec["alias_rows"].append((name, scope))
    return out


def names_for(conn, root: str) -> dict:
    """The agent's current name (its id when it was never renamed) and its old names."""
    rec = all_names(conn).get(base_id(root)) or {"name": None, "aliases": []}
    return {"id": base_id(root), "name": rec["name"] or base_id(root), "aliases": rec["aliases"]}


def main(argv=None):
    """`python3 -m scripts.identity_store.seat_names set <id> <name>` (the dashboard's Rename) or `get <id>`."""
    import argparse
    ap = argparse.ArgumentParser(description="Read or set an agent's name.")
    ap.add_argument("action", choices=["get", "set"])
    ap.add_argument("agent_id")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--by", default="dashboard")
    a = ap.parse_args(argv)
    from orchestra_cli.settings import data_dir
    od = os.environ.get("ORCHESTRA_DIR") or data_dir()
    if not os.path.exists(db_path_for(od)):
        # never create an identity store just to hold a name: an install without one has nothing to rename
        print(json.dumps({"ok": False, "error": "this install has no identity store yet, so agents can't be renamed"}))
        return 2
    try:
        conn = open_db(od) if a.action == "set" else read_only(od)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"identity store unavailable: {e}"}))
        return 2
    try:
        out = set_name(conn, a.agent_id, a.name or "", by=a.by, orchestra_dir=od) if a.action == "set" else names_for(conn, a.agent_id)
        print(json.dumps({"ok": True, **out}))
        return 0
    except NameError_ as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    except Exception as e:  # noqa: BLE001 - always an answer the dashboard can show
        print(json.dumps({"ok": False, "error": f"couldn't rename: {type(e).__name__}: {e}"}))
        return 2
    finally:
        conn.close()



def owner_of(conn, text: str, ids=None, scope=None, project_of=None):
    """Which agent a name or id belongs to (by key), or None: (root, how) with how in id|name|alias. With `scope`
    (a project slug, '' for none), only names in that project count: a name row given in it, or held by an agent
    that is in it now (project_of(root) -> slug). Ids and the assistant's name count everywhere."""
    key = name_key(text)
    if not key:
        return None
    rows = conn.execute("SELECT root, is_current, scope FROM seat_names WHERE name_key = ? ORDER BY is_current DESC",
                        (key,)).fetchall() if _has_table(conn) else []
    for r_root, cur, r_scope in rows:
        if scope is None or r_scope == scope or (project_of and project_of(r_root) == scope):
            return r_root, ("name" if cur else "alias")
    for i in (ids if ids is not None else known_ids(conn)):
        if name_key(i) == key:
            return i, "id"
    an = assistant_display_name()
    if an and name_key(an) == key:
        return "arturo", "name"
    return None


def set_name(conn, root: str, raw, by: str = "settings", orchestra_dir=None, project_of=None) -> dict:
    """Rename an agent. The new name becomes current, the old one an alias. Refused (NameError_) when the name
    is invalid, the agent never existed, or the name is taken: another agent's id or a built-in mailbox anywhere,
    or another agent's current or old name in the agent's own project. `project_of(root) -> slug or ''` (default:
    seat_projects.py over `orchestra_dir`)."""
    name = validate_name(raw)
    root = base_id(root)
    key = name_key(name)
    if project_of is None:
        from scripts.identity_store import seat_projects
        cards = seat_projects.project_cards(orchestra_dir) if orchestra_dir else {}
        graph = seat_projects.seat_graph(orchestra_dir) if orchestra_dir else {}
        project_of = lambda r: (seat_projects.project_of(r, cards=cards, graph=graph) or {}).get("slug", "")  # noqa: E731
    scope = project_of(root) or ""
    _ensure(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = known_ids(conn, orchestra_dir)
        if root not in ids or root in BUILTIN_IDS:
            raise NameError_(f"there is no agent with the id {root!r}" if root not in ids else
                             f"{root} is not an agent: the assistant is renamed in its own settings")
        owner = owner_of(conn, name, ids, scope=scope, project_of=lambda r: project_of(r) or "")
        if owner and owner[0] != root:
            how = {"id": "the id of", "name": "the name of", "alias": "an earlier name of"}[owner[1]]
            raise NameError_(f"{name!r} is already {how} {owner[0]}: every agent's names stay its own")
        now = _now()
        conn.execute("UPDATE seat_names SET is_current = 0 WHERE root = ? AND is_current = 1", (root,))
        if key == name_key(root):
            pass                                   # back to its id: no row needed, the id is always its own
        elif conn.execute("SELECT 1 FROM seat_names WHERE root = ? AND scope = ? AND name_key = ?", (root, scope, key)).fetchone():
            conn.execute("UPDATE seat_names SET is_current = 1, name = ?, set_at = ?, set_by = ? "
                         "WHERE root = ? AND scope = ? AND name_key = ?", (name, now, str(by)[:40], root, scope, key))
        else:
            conn.execute("INSERT INTO seat_names (root, scope, name_key, name, is_current, set_at, set_by) "
                         "VALUES (?, ?, ?, ?, 1, ?, ?)", (root, scope, key, name, now, str(by)[:40]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return names_for(conn, root)


def open_db(orchestra_dir):
    """A connection to the install's identity DB through the one factory (FK + busy timeout)."""
    return get_connection(db_path_for(orchestra_dir))


def read_only(orchestra_dir):
    """A read-only connection for resolvers: never takes a write lock, never creates the table (an install with
    no names yet has an empty name map)."""
    p = db_path_for(orchestra_dir)
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)


def all_names_ro(orchestra_dir) -> dict:
    try:
        conn = read_only(orchestra_dir)
    except sqlite3.Error:
        return {}
    try:
        return all_names(conn)
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
