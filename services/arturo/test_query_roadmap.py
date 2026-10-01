"""RED-first: query_roadmap reads the columns the roadmap tables actually have.

Arturo reported this against itself on staging, 2026-09-30: asked to "check the roadmap and
list the running agents", its own query_roadmap card came back empty and it fell back to
reading tasks.db by hand, then told the operator the tool "looks for a name column that
doesn't exist". Every column the query named was wrong, not just that one:

  r.name / rp.name  -> both tables have `title` (roadmaps also has a NOT NULL `project`)
  rt.title          -> roadmap_tasks has no title; the task's title lives in `tasks`,
                       reached through roadmap_tasks.task_id
  rp.sort_order     -> roadmap_phases has `phase_number`
  rt.sort_order     -> roadmap_tasks has no sort column; msg_store.py orders it by rowid

Schema of record: msg_store.py (the writer) and api/src/lib/db.ts.
"""
import importlib.util
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_qr", HERE / "arturo-proxy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def P(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = tmp_path
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return mod


def _seed(tmp_path, rows, roadmap_title="Identity Layer v1", project="orchestraos"):
    """Build tasks.db with the REAL schema, as msg_store.py / db.ts create it."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "state" / "tasks.db"))
    conn.executescript("""
        CREATE TABLE roadmaps (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'operator',
            project TEXT NOT NULL, pm_agent TEXT NOT NULL, title TEXT, vision TEXT,
            status TEXT DEFAULT 'draft', current_phase INTEGER DEFAULT 0,
            created_from TEXT, approved_at TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE roadmap_phases (
            id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL, phase_number INTEGER NOT NULL,
            title TEXT, description TEXT, status TEXT DEFAULT 'pending', milestone TEXT,
            started_at TEXT, completed_at TEXT);
        CREATE TABLE roadmap_tasks (
            id TEXT PRIMARY KEY, phase_id TEXT NOT NULL, task_id TEXT, agent_id TEXT,
            dependency_tasks TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'operator',
            title TEXT NOT NULL, description TEXT,
            status TEXT NOT NULL DEFAULT 'pending', created_by TEXT NOT NULL DEFAULT 'test');
    """)
    conn.execute("INSERT INTO roadmaps (id, project, pm_agent, title) VALUES ('r1', ?, 'pm', ?)",
                 (project, roadmap_title))
    phases = {}
    for i, (phase, title, status) in enumerate(rows):
        if phase not in phases:
            pid = f"p{len(phases)}"
            phases[phase] = pid
            conn.execute("INSERT INTO roadmap_phases (id, roadmap_id, phase_number, title)"
                         " VALUES (?, 'r1', ?, ?)", (pid, len(phases), phase))
        conn.execute("INSERT INTO tasks (id, title, created_by) VALUES (?, ?, 'test')",
                     (f"t{i}", title))
        conn.execute("INSERT INTO roadmap_tasks (id, phase_id, task_id, status)"
                     " VALUES (?, ?, ?, ?)", (f"rt{i}", phases[phase], f"t{i}", status))
    conn.commit()
    conn.close()


ROWS = [("Reconcile", "Close the sid gap", "completed"),
        ("Reconcile", "Backfill ctx freshness", "in_progress"),
        ("Arm", "Arm the conformant pool", "pending")]


# ---- the bug the operator hit ----------------------------------------------------------------

def test_a_seeded_roadmap_is_reported_not_an_sql_error(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "all"})
    assert "error" not in out.lower(), out
    assert "no roadmap data" not in out.lower(), out


def test_every_task_title_and_its_phase_appear(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "all"})
    for _, title, _ in ROWS:
        assert title in out, (title, out)
    assert "Reconcile" in out and "Arm" in out
    assert "Identity Layer v1" in out


def test_status_shows_as_its_icon(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "all"})
    assert "✓ Close the sid gap" in out
    assert "→ Backfill ctx freshness" in out
    assert "○ Arm the conformant pool" in out


def test_phases_come_back_in_phase_number_order(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "all"})
    assert out.index("Reconcile") < out.index("Arm")
    # and the tasks inside a phase keep insertion order, as msg_store.py lists them
    assert out.index("Close the sid gap") < out.index("Backfill ctx freshness")


# ---- the filters the tool advertises ---------------------------------------------------------

def test_the_status_filter_narrows_to_that_status(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "in_progress"})
    assert "Backfill ctx freshness" in out
    assert "Close the sid gap" not in out and "Arm the conformant pool" not in out


def test_a_project_is_matched_by_its_roadmap_title(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "identity", "filter": "all"})
    assert "Close the sid gap" in out, out


def test_a_project_is_matched_by_its_project_column_too(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "orchestraos", "filter": "all"})
    assert "Close the sid gap" in out, out


def test_an_unknown_project_says_so_rather_than_erroring(P, tmp_path):
    _seed(tmp_path, ROWS)
    out = P.execute_tool("query_roadmap", {"project": "no-such-thing", "filter": "all"})
    assert "no roadmap data" in out.lower()
    assert "error" not in out.lower()


# ---- the shapes the columns allow ------------------------------------------------------------

def test_a_roadmap_with_no_title_falls_back_to_its_project(P, tmp_path):
    _seed(tmp_path, ROWS, roadmap_title=None, project="sanctuary")
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "all"})
    assert "sanctuary" in out, out
    assert "None" not in out, out


def test_a_roadmap_task_with_no_task_row_is_still_listed(P, tmp_path):
    """roadmap_tasks.task_id is nullable and may point at a task that was never written."""
    _seed(tmp_path, ROWS)
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "state" / "tasks.db"))
    conn.execute("INSERT INTO roadmap_tasks (id, phase_id, task_id, status)"
                 " VALUES ('rtX', 'p0', 'missing-task', 'blocked')")
    conn.commit()
    conn.close()
    out = P.execute_tool("query_roadmap", {"project": "all", "filter": "all"})
    assert "error" not in out.lower(), out
    assert "✗" in out, out
    assert "None" not in out, out
