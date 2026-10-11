"""`orchestra identity arm`: make the identity database the source of truth on this data dir.

OrchestraOS keeps every seat's identity (lineage, generations and their session ids, the
canonical pointer and status) in one SQLite database, ``state/orchestra-registry.db``. The
flat files most readers use (``registry.json``, ``state/agent-sessions.json``,
``state/agents/*.json``) are projections of it, rewritten by the regenerator daemon.

That only holds once the cutover is ARMED (``state/identity-store-cutover.flag``). Disarmed,
every identity writer falls back to writing the flat files directly; nothing records a seat's
session id or moves it off ``provisioning``, so boot recovery has nothing to resume and the
identity reconciler refuses to run. ``orchestra init`` arms a new data dir; this command arms
one that already has seats, in the order the reference install used:

  1. back up the flat files (and any existing database) under ``backups/identity-arm-<ts>/``,
     and rewrite an older starter's ``machine: "local"`` to ``"vps"`` (this host's name everywhere
     else, so recovery and the reconcilers act on those seats)
  2. arm the projection-liveness monitor (before the switch, so a stale projection pages)
  3. freeze identity writers, import the flat files into the database, re-project them into a
     scratch dir and require a ZERO semantic diff against the originals
  4. arm the cutover, unfreeze, and start the regenerator

Any failure before step 4 leaves the box exactly as it was (the database it created is removed,
the monitor disarmed, writers unfrozen). ``--dry-run`` does steps 1-3 against a scratch copy.
Rollback after arming: ``python3 -m scripts.identity_store.cutover`` has ``rollback()``; the
flat files keep working because they are always current.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

DB_REL = Path("state") / "orchestra-registry.db"
SESSIONS_REL = Path("state") / "agent-sessions.json"
AGENTS_REL = Path("state") / "agents"


class ArmError(RuntimeError):
    """Arming refused; the box is unchanged."""


def _modules(repo_root: Path):
    for p in (str(repo_root), str(repo_root / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from scripts.identity_store import cutover, freeze, migrate, monitor, orchestra_db, regenerator
    return cutover, freeze, migrate, monitor, orchestra_db, regenerator


def _has_identity_rows(db: Path) -> bool:
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(str(db))
        try:
            return conn.execute("SELECT COUNT(*) FROM canonical").fetchone()[0] > 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


# Older `orchestra starter` builds wrote machine "local" for seats on this host. Everything that
# acts on a seat (spawn-agent.sh, boot recovery, the reconcilers) calls this host "vps" and treats
# any other value as a seat running elsewhere, so those seats were never resumed or reconciled.
_THIS_HOST_ALIASES = ("local",)


def normalize_machine(registry_path: Path) -> list:
    """Rewrite this-host aliases to "vps" in registry.json. Returns the seats changed."""
    reg = json.loads(registry_path.read_text())
    changed = [name for name, a in (reg.get("agents") or {}).items()
               if isinstance(a, dict) and a.get("machine") in _THIS_HOST_ALIASES]
    if changed:
        for name in changed:
            reg["agents"][name]["machine"] = "vps"
        tmp = registry_path.with_name(registry_path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(reg, indent=2) + "\n")
        os.replace(tmp, registry_path)
    return changed


def _import_and_verify(repo_root: Path, data_dir: Path, db: Path) -> dict:
    """Import the flat files into ``db`` and prove the round trip loses nothing."""
    _, _, migrate, _, orchestra_db, _ = _modules(repo_root)
    orchestra_db.init_db(str(db))
    conn = orchestra_db.get_connection(str(db))
    try:
        rep = migrate.migrate(conn, str(data_dir / "registry.json"), str(data_dir / SESSIONS_REL),
                              str(data_dir / AGENTS_REL))
        with tempfile.TemporaryDirectory(prefix="identity-arm-") as scratch:
            migrate.reproject(conn, scratch)
            diffs = migrate.diff_report(str(data_dir), scratch)
    finally:
        conn.close()
    rep["diffs"] = diffs
    return rep


def arm(repo_root: Path, data_dir: Path, *, dry_run: bool = False, start_regenerator: bool = True,
        now: float | None = None) -> dict:
    """Arm the identity store on ``data_dir``. Returns a report; raises ArmError (box unchanged)."""
    repo_root, data_dir = Path(repo_root), Path(data_dir)
    cutover, freeze, _, monitor, _, regenerator = _modules(repo_root)
    od = str(data_dir)
    if cutover.is_active(od):
        started = regenerator.start_if_armed(od) if start_regenerator and not dry_run else False
        return {"armed": True, "already": True, "regenerator_started": started}
    for rel in ("registry.json", str(SESSIONS_REL)):
        p = data_dir / rel
        if not p.exists():
            raise ArmError(f"{p} is missing; run `orchestra init` first")
        try:
            json.loads(p.read_text())
        except ValueError as e:
            raise ArmError(f"{p} is not valid JSON ({e}); fix it before arming") from e
    db = data_dir / DB_REL
    if _has_identity_rows(db):
        raise ArmError(f"{db} already holds identity rows but the cutover is off: a previous arm was "
                       "rolled back or half-done. Move that database aside (it is the old truth) and re-run.")

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="identity-arm-dry-") as tmp:
            copy = Path(tmp) / "data"
            (copy / "state").mkdir(parents=True)
            shutil.copy2(data_dir / "registry.json", copy / "registry.json")
            shutil.copy2(data_dir / SESSIONS_REL, copy / SESSIONS_REL)
            if (data_dir / AGENTS_REL).is_dir():
                shutil.copytree(data_dir / AGENTS_REL, copy / AGENTS_REL)
            normalized = normalize_machine(copy / "registry.json")
            rep = _import_and_verify(repo_root, copy, Path(tmp) / "orchestra-registry.db")
        rep.update(armed=False, dry_run=True, machine_normalized=normalized)
        if rep["diffs"]:
            raise ArmError("dry run: the flat files do not round-trip through the database:\n  "
                           + "\n  ".join(rep["diffs"][:20]))
        return rep

    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now))
    backup = data_dir / "backups" / f"identity-arm-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(data_dir / "registry.json", backup / "registry.json")
    shutil.copy2(data_dir / SESSIONS_REL, backup / "agent-sessions.json")
    if (data_dir / AGENTS_REL).is_dir():
        shutil.copytree(data_dir / AGENTS_REL, backup / "agents", dirs_exist_ok=True)
    if db.exists():
        shutil.copy2(db, backup / "orchestra-registry.db")

    monitor_was_armed = monitor.is_armed(od)
    db_existed = db.exists()
    monitor.arm(od)
    freeze.freeze(od)
    normalized = []
    try:
        normalized = normalize_machine(data_dir / "registry.json")
        rep = _import_and_verify(repo_root, data_dir, db)
        if rep["diffs"]:
            raise ArmError("the flat files do not round-trip through the database, so arming would "
                           "change what readers see:\n  " + "\n  ".join(rep["diffs"][:20]))
        cutover.arm(od)
    except Exception:
        if not db_existed:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(str(db) + suffix)
                except FileNotFoundError:
                    pass
        else:
            shutil.copy2(backup / "orchestra-registry.db", db)
        if normalized:
            shutil.copy2(backup / "registry.json", data_dir / "registry.json")
        if not monitor_was_armed:
            monitor.disarm(od)
        raise
    finally:
        freeze.unfreeze(od)
    started = regenerator.start_if_armed(od) if start_regenerator else False
    rep.update(armed=True, already=False, backup=str(backup), regenerator_started=started,
               machine_normalized=normalized)
    return rep


def record_seat(repo_root: Path, data_dir: Path, name: str, row: dict) -> bool:
    """Put a seat the CLI just wrote into registry.json into the identity database too.

    Once the store is armed, registry.json is a projection: the regenerator rewrites it from the
    database within ~30 s, so a row that exists only in the flat file (the starter's new seat,
    `orchestra agent`'s, init's --demo seats) is erased, often before spawn-agent.sh registers it.
    This imports exactly that one row (never the whole flat file, which may be older than the
    database for other seats) and projects synchronously. A seat already in the database gets its
    registry doc updated through the sanctioned writer instead. Returns False when not armed."""
    cutover, _, migrate, _, orchestra_db, _ = _modules(repo_root)
    od = str(data_dir)
    if not cutover.is_active(od):
        return False
    from scripts.identity_store import identity_writer, status_vocab
    # canonical.status only takes the four-value enum (online / parked / provisional / retired).
    # The CLI's "provisioning" (not started yet) and the demo seats' "offline" are not in it; with
    # no live process both are parked there, and the reconciler flips the seat to online the first
    # pass its pane is live. The registry DOC keeps the written status verbatim: the message
    # router's bootstrap exemption reads "provisioning" from it to let a newborn's orientation in.
    typed = row
    if row.get("status") not in status_vocab.STATUS_ENUM:
        typed = dict(row, status=status_vocab.resolve(row.get("status"), is_live=False))
    db = Path(data_dir) / DB_REL
    orchestra_db.init_db(str(db))
    conn = orchestra_db.get_connection(str(db))
    try:
        exists = conn.execute("SELECT 1 FROM canonical WHERE root=?", (name,)).fetchone() is not None
        doc = conn.execute("SELECT payload_json FROM source_records WHERE file='registry.json' "
                           "AND kind='agent' AND key=?", (name,)).fetchone()
        current = json.loads(doc[0]) if doc else {}
        if not exists:
            nxt = conn.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM source_records "
                               "WHERE file='registry.json' AND kind='agent'").fetchone()[0]
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate._store_source(conn, "registry.json", "agent", name, nxt, row)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conflicts = []
            sess = {name: {"model": row["model"]}} if row.get("model") else {}
            migrate._populate_typed(conn, {name: typed}, sess, {}, conflicts)
            conn.commit()
            if conflicts:
                raise RuntimeError(f"could not record {name} in the identity store: {conflicts}")
    finally:
        conn.close()
    if exists:
        # a later CLI edit of a registered seat (orchestra agent's reports_to fix-up): its config
        # only. Status belongs to spawn and the reconciler, never to this re-write.
        cfg = {k: v for k, v in row.items() if k != "status"}
        identity_writer.update_registry_agent(od, name, cfg, full_record=dict(current, **cfg))
    identity_writer.project_now(od)
    return True


def status(repo_root: Path, data_dir: Path) -> dict:
    cutover, freeze, _, monitor, _, _ = _modules(repo_root)
    od = str(data_dir)
    return {"cutover": cutover.is_active(od), "monitor": monitor.is_armed(od),
            "frozen": freeze.is_frozen(od), "database": (Path(data_dir) / DB_REL).exists(),
            "identity_rows": _has_identity_rows(Path(data_dir) / DB_REL)}


def cmd_identity(ns) -> int:
    from . import settings as S
    st = S.load_settings()
    data_dir = Path(ns.data_dir) if getattr(ns, "data_dir", None) else st.data_dir
    if ns.identity_cmd == "status":
        s = status(st.repo_root, data_dir)
        print(json.dumps(s, indent=2) if ns.json else
              "\n".join(f"{k:14} {'yes' if v else 'no'}" for k, v in s.items()))
        return 0
    try:
        rep = arm(st.repo_root, data_dir, dry_run=ns.dry_run)
    except ArmError as e:
        print(f"identity arm: refused, nothing changed: {e}", file=sys.stderr)
        return 2
    if rep.get("already"):
        print(f"identity store already armed on {data_dir}"
              + ("; started the regenerator" if rep.get("regenerator_started") else ""))
    elif rep.get("dry_run"):
        print(f"dry run OK: {rep['typed_populated']} seats would import with zero drift"
              + (f"; {len(rep['typed_conflicts'])} conflicts: {rep['typed_conflicts']}" if rep["typed_conflicts"] else ""))
    else:
        print(f"armed the identity store on {data_dir}: {rep['typed_populated']} seats imported with zero drift; "
              f"backup in {rep['backup']}; regenerator {'started' if rep['regenerator_started'] else 'already running'}")
        if rep["typed_conflicts"]:
            print(f"conflicts (resolved, review them): {rep['typed_conflicts']}")
    if rep.get("machine_normalized"):
        print(f"machine \"local\" -> \"vps\" (this host) for: {', '.join(rep['machine_normalized'])}")
    return 0
