"""A project's seats work in its folder, and a new PM makes its project, card included."""
import json

from orchestra_cli import __main__ as M
from orchestra_cli import projects as PJ
from orchestra_cli import seats as SE
from orchestra_cli.tests.test_seats import repo, repo_with_templates, _register, _starter_env  # noqa: F401


def test_a_project_is_a_folder_and_a_card(tmp_path):
    folder = PJ.ensure_project(tmp_path, "north-star-bakery", pm="pm-north-star-bakery")
    assert folder == tmp_path / "projects" / "north-star-bakery" and folder.is_dir()
    card = PJ.load(tmp_path)["north-star-bakery"]
    assert card["name"] == "North Star Bakery" and card["pm"] == "pm-north-star-bakery" and card["status"] == "active"
    PJ.ensure_project(tmp_path, "north-star-bakery", agents=("dev-north-star-bakery",))
    PJ.ensure_project(tmp_path, "acme", pm="pm-acme")
    data = PJ.load(tmp_path)
    assert data["north-star-bakery"]["agents"] == ["pm-north-star-bakery", "dev-north-star-bakery"]
    assert data["acme"]["priority"] == 2
    assert PJ.project_of(tmp_path, "dev-north-star-bakery") == "north-star-bakery"
    assert PJ.slug_for_seat("gm") is None and PJ.slug_for_seat("pm-acme") == "acme"


def test_a_new_pm_makes_its_project_and_starts_in_its_folder(repo_with_templates, tmp_path, monkeypatch):
    _register(tmp_path, gm="T0")
    calls = _starter_env(monkeypatch)
    rc = M.main(["agent", "create", "pm-acme", "--tier", "T1", "--template", "pm", "--parent", "gm",
                 "--set", "PROJECT=acme", "--set", "CLIENT_NAME=none yet", "--set", "CLIENT_SLUG=acme",
                 "--set", "BRANCH=main"])
    assert rc == 0 and calls
    data = tmp_path / "data"
    row = json.loads((data / "registry.json").read_text())["agents"]["pm-acme"]
    assert row["cwd"] == str(data / "projects" / "acme")
    assert PJ.load(data)["acme"]["pm"] == "pm-acme"


def test_a_pms_worker_works_in_the_projects_folder_and_the_manager_does_not(repo_with_templates, tmp_path, monkeypatch):
    _register(tmp_path, gm="T0", **{"pm-acme": "T1"})
    _starter_env(monkeypatch)
    assert M.main(["agent", "create", "qa-acme", "--parent", "pm-acme", "--template", "dev",
                   "--set", "PROJECT=acme"]) == 0
    data = tmp_path / "data"
    reg = json.loads((data / "registry.json").read_text())["agents"]
    assert reg["qa-acme"]["cwd"] == str(data / "projects" / "acme")
    assert "qa-acme" in PJ.load(data)["acme"]["agents"]
    assert M.main(["agent", "create", "helper", "--parent", "gm"]) == 0
    reg = json.loads((data / "registry.json").read_text())["agents"]
    assert reg["helper"]["cwd"] == str(repo_with_templates)       # a lead under the manager: the checkout


def test_the_starter_team_gets_its_project_card(repo_with_templates, tmp_path, monkeypatch):
    spawned = set()
    calls = _starter_env(monkeypatch)
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in spawned)
    assert M.main(["starter", "--project", "website"]) == 0
    data = tmp_path / "data"
    reg = json.loads((data / "registry.json").read_text())["agents"]
    assert reg["pm-website"]["cwd"] == reg["dev-website"]["cwd"] == str(data / "projects" / "website")
    assert reg["gm"]["cwd"] == str(repo_with_templates)
    card = PJ.load(data)["website"]
    assert card["pm"] == "pm-website" and "dev-website" in card["agents"]


def test_every_project_gets_the_same_layout_and_a_project_md(tmp_path):
    folder = PJ.ensure_project(tmp_path, "acme", name="Acme Widgets Co", pm="pm-acme")
    for d in PJ.LAYOUT:
        assert (folder / d).is_dir()
    md = (folder / "PROJECT.md").read_text()
    assert md.startswith("# Acme Widgets Co") and "run by pm-acme" in md and "`deliverables/`" in md
    (folder / "PROJECT.md").write_text("# edited by the PM\n")
    PJ.ensure_project(tmp_path, "acme")
    assert (folder / "PROJECT.md").read_text() == "# edited by the PM\n"          # never overwritten
    assert PJ.unfiled(["PROJECT.md", "deliverables/a.md", "stray.txt", "misc/b.png", "site/index.html"]) == \
        ["stray.txt", "misc/b.png"]
