"""RED (BG leg-(ii) P0.4 — real gen-suffixed tmux binding at promote).

execute_swap + build_swap_documents write canonical.tmux_session = ``root`` literally,
but the green lives in tmux session ``{root}-g{N}``. So after a bg swap, canonical
points at a DEAD pane (the pane named ``root`` is blue's, now retired) — pane routers
and Arturo route to nothing (leg-(i) needed a hand ``tmux rename`` to paper over this).
Astra §C: write the ACTUAL gen-suffixed ``{root}-g{N}`` binding into canonical + the
projected documents at promotion (immutable execution binding) — NOT rename the pane.

Delivery-critical (promote). Drives the REAL bg swap seam against a REAL
orchestra-registry.db and asserts the on-disk canonical.tmux_session BY EFFECT. Also
locks the back-compat default so promote_successor/rotate_agent are byte-identical.

RED until execute_swap honors green["tmux_session"], make_swap_fn supplies {root}-g{N},
and build_swap_documents emits the gen-suffixed binding.
"""
import os

from identity_store.orchestra_db import get_connection, init_db, execute_swap
from lineage_daemon.wal.real_seams import make_swap_fn
from lineage_daemon.wal.swap_documents import build_swap_documents

ROOT = "identity-store-builder"


def _seed(tmp_path):
    od = str(tmp_path)
    os.makedirs(os.path.join(od, "state"), exist_ok=True)
    db = os.path.join(od, "state", "orchestra-registry.db")
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)", (ROOT, "T2", "claude"))
    blue = conn.execute("INSERT INTO generations (root, generation, model) "
                        "VALUES (?, 2, 'claude-opus-4-8[1m]')", (ROOT,)).lastrowid
    conn.execute("INSERT INTO runtime_state (generation_id, status) VALUES (?, 'online')", (blue,))
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session) VALUES (?, ?, ?)",
                 (ROOT, blue, ROOT))
    conn.close()
    os.environ["IDENTITY_STORE_CUTOVER"] = "1"
    return od, db


def teardown_function(_):
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)


def _blue_record():
    return {"name": ROOT, "generation": 2, "session_id": "blue-sid-2", "status": "online", "tier": "T2"}


def _canonical_tmux(db):
    conn = get_connection(db)
    try:
        row = conn.execute("SELECT tmux_session FROM canonical WHERE root=?", (ROOT,)).fetchone()
        return row["tmux_session"] if row else None
    finally:
        conn.close()


def test_bg_swap_binds_gen_suffixed_tmux_session(tmp_path):
    """The delivery-critical invariant: after a bg swap, canonical.tmux_session
    resolves to the LIVE green pane {root}-g{N}, not the dead 'root' pane."""
    od, db = _seed(tmp_path)
    green = {"generation": 3, "session_id": "green-sid-3", "model": "m"}
    make_swap_fn(od, blue_record=_blue_record())(ROOT, green, blue_generation_id=None)
    assert _canonical_tmux(db) == f"{ROOT}-g3", \
        "P0.4: canonical.tmux_session must be the gen-suffixed green pane, not 'root'"


def test_execute_swap_defaults_tmux_session_to_root(tmp_path):
    """Back-compat: promote_successor/rotate_agent pass a green with NO tmux_session
    => canonical.tmux_session stays 'root' (byte-identical legacy)."""
    _, db = _seed(tmp_path)
    conn = get_connection(db)
    try:
        execute_swap(conn, ROOT, green={"generation": 3, "session_id": "s3", "model": "m"},
                     blue_generation_id=None, now="t2")
    finally:
        conn.close()
    assert _canonical_tmux(db) == ROOT


def test_execute_swap_honors_explicit_tmux_session(tmp_path):
    _, db = _seed(tmp_path)
    conn = get_connection(db)
    try:
        execute_swap(conn, ROOT,
                     green={"generation": 3, "session_id": "s3", "model": "m",
                            "tmux_session": f"{ROOT}-g3"},
                     blue_generation_id=None, now="t2")
    finally:
        conn.close()
    assert _canonical_tmux(db) == f"{ROOT}-g3"


def test_build_swap_documents_carries_the_binding(tmp_path):
    # make_swap_fn sets green["tmux_session"]={root}-g{N} before building docs; the
    # doc builder must CARRY it into the successor records (so registry/session/state
    # agree with canonical), not hardcode root.
    green = {"generation": 3, "session_id": "s3", "model": "m",
             "tmux_session": f"{ROOT}-g3"}
    docs = build_swap_documents(root=ROOT, blue_generation=2, green=green,
                                blue_record=_blue_record())
    seen = 0
    for file, kind, key, record in docs:
        if key == ROOT and "tmux_session" in record:
            seen += 1
            assert record["tmux_session"] == f"{ROOT}-g3", \
                f"P0.4: {file}/{kind} tmux_session must carry the binding, got {record['tmux_session']!r}"
    assert seen >= 1, "expected at least one successor record carrying tmux_session"


def test_build_swap_documents_defaults_root_without_binding(tmp_path):
    # non-bg / no-binding path stays byte-identical (root).
    docs = build_swap_documents(root=ROOT, blue_generation=2,
                                green={"generation": 3, "session_id": "s3", "model": "m"},
                                blue_record=_blue_record())
    for file, kind, key, record in docs:
        if key == ROOT and "tmux_session" in record:
            assert record["tmux_session"] == ROOT
