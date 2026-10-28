"""Seat names: one changeable name per agent on top of its permanent id; old names stay its own forever;
one namespace across ids, names and old names, living or dead; names follow the lineage, not a generation."""
import json

import pytest

from scripts.identity_store import orchestra_db as odb
from scripts.identity_store import seat_names as sn


def _lineage(conn, root, reports_to=None):
    conn.execute("INSERT INTO lineages (root, tier, runtime, reports_to) VALUES (?, 'T1', 'claude', ?)", (root, reports_to))


@pytest.fixture
def conn(tmp_path):
    db = str(tmp_path / "state" / "orchestra-registry.db")
    (tmp_path / "state").mkdir()
    odb.init_db(db)
    c = odb.get_connection(db)
    for root, parent in (("gm", None), ("pm-demo", "gm"), ("dev-demo", "pm-demo"),
                         ("qa-demo", "pm-demo"), ("pm-first-project", "gm")):   # the last one retired long ago
        _lineage(c, root, parent)
    yield c
    c.close()


def test_never_renamed_is_its_id(conn):
    assert sn.names_for(conn, "pm-demo") == {"id": "pm-demo", "name": "pm-demo", "aliases": []}
    assert sn.all_names(conn) == {}                    # a read never creates the table


def test_rename_keeps_the_old_name_as_an_alias_forever(conn):
    sn.set_name(conn, "pm-demo", "Demo", by="operator")
    assert sn.names_for(conn, "pm-demo") == {"id": "pm-demo", "name": "Demo", "aliases": []}
    sn.set_name(conn, "pm-demo", "Example")
    assert sn.names_for(conn, "pm-demo") == {"id": "pm-demo", "name": "Example", "aliases": ["Demo"]}
    assert sn.owner_of(conn, "demo") == ("pm-demo", "alias")
    assert sn.owner_of(conn, "EXAMPLE") == ("pm-demo", "name")
    assert sn.owner_of(conn, "pm demo") == ("pm-demo", "id")


def test_a_generation_shares_its_lineages_name(conn):
    sn.set_name(conn, "pm-demo-gen3", "Demo")         # set through a generation's id: the lineage's name
    assert sn.names_for(conn, "pm-demo-g4")["name"] == "Demo"


def test_one_namespace_ids_names_and_old_names_living_or_dead(conn):
    sn.set_name(conn, "pm-demo", "Demo")
    sn.set_name(conn, "pm-demo", "Example")                  # "Demo" is now its old name
    for root, taken, reason in (("qa-demo", "Demo", "an earlier name of pm-demo"),
                                ("qa-demo", "example", "the name of pm-demo"),
                                ("qa-demo", "DEV demo", "the id of dev-demo"),
                                ("qa-demo", "pm first project", "the id of pm-first-project")):   # a dead agent's id
        with pytest.raises(sn.NameError_, match=reason):
            sn.set_name(conn, root, taken)


def test_an_agent_can_take_back_its_own_old_name_or_its_id(conn):
    sn.set_name(conn, "pm-demo", "Demo")
    sn.set_name(conn, "pm-demo", "Example")
    assert sn.set_name(conn, "pm-demo", "demo")["name"] == "demo"     # its own old name, any case
    assert sn.names_for(conn, "pm-demo")["aliases"] == ["Example"]
    back = sn.set_name(conn, "pm-demo", "pm-demo")                         # back to its id
    assert back["name"] == "pm-demo" and sorted(back["aliases"]) == ["Example", "demo"]


def test_unknown_agent_and_bad_names_are_refused(conn):
    with pytest.raises(sn.NameError_, match="no agent"):
        sn.set_name(conn, "nobody", "Ghost")
    for bad in ("", "  ", "x" * 41, "-lead", "trail-", "123", "a  -b", "<b>", "x: y"):
        with pytest.raises(sn.NameError_):
            sn.set_name(conn, "gm", bad)
    assert sn.validate_name("  Inspiration   Bot ") == "Inspiration Bot"
    assert sn.validate_name("O'Neil-2.0") == "O'Neil-2.0"
    assert sn.validate_name("Shore\nline") == "Shore line"          # a line break is tidied away, never kept


def test_a_refused_rename_changes_nothing(conn):
    sn.set_name(conn, "pm-demo", "Demo")
    with pytest.raises(sn.NameError_):
        sn.set_name(conn, "qa-demo", "de mo")
    assert sn.all_names(conn) == {"pm-demo": {"name": "Demo", "scope": "", "aliases": [], "alias_rows": []}}


def test_ids_known_only_to_the_registry_files_count_too(conn, tmp_path):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {"inspiration": {"tmux_session": "inspiration"}}}))
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps({"scout-g2": {"lineage_root": "scout"}}))
    ids = sn.known_ids(conn, tmp_path)
    assert {"inspiration", "scout", "gm"} <= ids and "scout-g2" not in ids
    sn.set_name(conn, "inspiration", "Muse", orchestra_dir=tmp_path)
    with pytest.raises(sn.NameError_, match="the id of scout"):
        sn.set_name(conn, "gm", "Scout", orchestra_dir=tmp_path)


def test_read_only_reader_sees_names_and_never_creates_the_table(conn, tmp_path):
    assert sn.all_names_ro(tmp_path) == {}
    ro = sn.read_only(tmp_path)
    assert not ro.execute("SELECT 1 FROM sqlite_master WHERE name='seat_names'").fetchone()
    ro.close()
    sn.set_name(conn, "gm", "Chief")
    assert sn.all_names_ro(tmp_path) == {"gm": {"name": "Chief", "scope": "", "aliases": [], "alias_rows": []}}


def test_the_built_in_mailboxes_and_the_assistants_name_are_taken(conn, monkeypatch):
    monkeypatch.setattr(sn, "assistant_display_name", lambda: "Example")
    for taken, reason in (("Arturo", "the id of arturo"), ("operator", "the id of operator"), ("EXAMPLE", "the name of arturo")):
        with pytest.raises(sn.NameError_, match=reason):
            sn.set_name(conn, "gm", taken)
    with pytest.raises(sn.NameError_, match="not an agent"):
        sn.set_name(conn, "arturo", "Helper")



# ---- names unique per project ----

def _projects(tmp_path, mapping):
    """projects.json with one card per project ({slug: pm}), and reports_to for the workers."""
    (tmp_path / "state" / "knowledge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "knowledge" / "projects.json").write_text(json.dumps(
        {slug: {"name": slug.title(), "pm": pm} for slug, pm in mapping.items()}))


def test_two_projects_can_each_have_a_dev_but_one_project_cannot_have_two(conn, tmp_path):
    for root, parent in (("pm-other", "gm"), ("dev-other", "pm-other"), ("qa-other", "pm-other")):
        conn.execute("INSERT INTO lineages (root, tier, runtime, reports_to) VALUES (?, 'T2', 'claude', ?)", (root, parent))
    _projects(tmp_path, {"demo": "pm-demo", "other": "pm-other"})
    proj = {"pm-demo": "demo", "dev-demo": "demo", "qa-demo": "demo", "pm-other": "other", "dev-other": "other", "qa-other": "other"}
    po = lambda r: proj.get(r, "")
    sn.set_name(conn, "dev-demo", "dev", project_of=po)
    sn.set_name(conn, "dev-other", "dev", project_of=po)                  # another project: fine
    with pytest.raises(sn.NameError_, match="already the name of dev-demo"):
        sn.set_name(conn, "qa-demo", "Dev", project_of=po)               # same project: taken
    with pytest.raises(sn.NameError_, match="the id of gm"):
        sn.set_name(conn, "qa-other", "GM", project_of=po)               # ids count everywhere


def test_an_old_name_stays_taken_in_its_project_and_follows_the_scope_it_was_given_in(conn):
    proj = {"pm-demo": "demo", "dev-demo": "demo", "qa-demo": "demo"}
    po = lambda r: proj.get(r, "")
    sn.set_name(conn, "dev-demo", "builder", project_of=po)
    sn.set_name(conn, "dev-demo", "maker", project_of=po)                  # "builder" is its old name now
    with pytest.raises(sn.NameError_, match="an earlier name of dev-demo"):
        sn.set_name(conn, "qa-demo", "builder", project_of=po)
    rec = sn.all_names(conn)["dev-demo"]
    assert rec["name"] == "maker" and rec["scope"] == "demo" and rec["alias_rows"] == [("builder", "demo")]
    proj["dev-demo"] = ""                                                  # moved out of the project
    with pytest.raises(sn.NameError_, match="an earlier name of dev-demo"):
        sn.set_name(conn, "qa-demo", "builder", project_of=po)             # given in demo: still taken there
    with pytest.raises(sn.NameError_, match="the name of dev-demo"):
        sn.set_name(conn, "pm-demo", "maker", project_of=po)               # "Demo · maker" stays dev-demo's old full name
    sn.set_name(conn, "pm-demo", "lead", project_of=po)                    # a name nobody in demo has had: fine


def test_the_command_line_runs_with_every_function_defined(tmp_path):
    """The rename route runs this module as a script: a refusal comes back as JSON, never a NameError."""
    import subprocess, sys as _sys, os as _os
    (tmp_path / "state").mkdir(exist_ok=True)
    r = subprocess.run([_sys.executable, "-m", "scripts.identity_store.seat_names", "set", "nobody", "Example"],
                       capture_output=True, text=True, env={**_os.environ, "ORCHESTRA_DIR": str(tmp_path)})
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["ok"] is False and "not defined" not in out["error"]
