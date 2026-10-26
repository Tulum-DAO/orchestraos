"""A project: its folder (<data dir>/projects/<slug>) and its card on the Projects page
(<data dir>/state/knowledge/projects.json, which api/src/routes/projects.ts reads).

A user report: "if an agent is a part of a project, it should be working in that project folder"
and "any time there's a new project manager there should be a new project ... a card within the projects
page." A PM seat creates its project; the project's seats (the PM, its dev and qa workers) work in its folder.
The top-level manager stays in the harness checkout.
"""
import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# Every project's folder has the same shape, so what agents build is where the operator looks for it
# (a user report: "organized properly and appear within organized folders"). PROJECT.md says it
# to every agent that opens the folder; the files page flags anything outside it as unfiled.
LAYOUT = {
    "deliverables": "finished work for the operator: reports, audits, decks (dated: YYYY-MM-DD-short-name.ext)",
    "site": "the code (its own git repo when there is one)",
    "research": "sources, notes, competitor pages",
    "assets": "images, logos, copy",
    "drafts": "work in progress; moves to deliverables/ when done",
}
TOP_LEVEL_FILES = ("PROJECT.md", "README.md")


def project_md(name, slug, pm=None):
    lines = [f"# {name}", "", f"Project `{slug}`" + (f", run by {pm}." if pm else "."), "",
             "## Where things go", ""]
    lines += [f"- `{d}/` — {what}" for d, what in LAYOUT.items()]
    lines += ["", "Nothing else at the top level except this file and README.md. A file that fits nowhere: ask the PM,",
              "who decides and adds the place here. Never put this project's files in the OrchestraOS checkout.", ""]
    return "\n".join(lines)


def scaffold(folder, name, slug, pm=None):
    """The layout's folders and PROJECT.md, created if missing; nothing existing is touched."""
    for d in LAYOUT:
        (Path(folder) / d).mkdir(parents=True, exist_ok=True)
    md = Path(folder) / "PROJECT.md"
    if not md.exists():
        md.write_text(project_md(name, slug, pm))


def unfiled(paths):
    """The files outside the layout (relative paths), for the files page to flag."""
    out = []
    for p in paths:
        top = p.split("/", 1)[0]
        if ("/" not in p and p not in TOP_LEVEL_FILES) or ("/" in p and top not in LAYOUT):
            out.append(p)
    return out


ROLE_PREFIXES = ("pm", "dev", "qa", "worker")


def projects_file(data_dir) -> Path:
    return Path(data_dir) / "state" / "knowledge" / "projects.json"


def project_dir(data_dir, slug) -> Path:
    return Path(data_dir) / "projects" / slug


def slug_for_seat(seat):
    """'pm-acme' -> 'acme', 'pm-north-star-bakery' -> 'north-star-bakery'; None for a seat whose name
    is not role-project shaped."""
    parts = (seat or "").strip().lower().split("-", 1)
    if len(parts) == 2 and parts[0] in ROLE_PREFIXES and SLUG_RE.match(parts[1]):
        return parts[1]
    return None


def title_for(slug):
    """'north-star-bakery' -> 'North Star Bakery'; '24-hour-gym' -> '24 Hour Gym'."""
    return " ".join(w.capitalize() if not w.isdigit() else w for w in slug.split("-"))


@contextmanager
def _locked(data_dir):
    path = projects_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".json.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def load(data_dir):
    return _read(projects_file(data_dir))


def ensure_project(data_dir, slug, *, name=None, pm=None, agents=()):
    """The project's folder and its card, created if missing (an existing card keeps what it has; the pm and
    agents given are added). Returns the folder."""
    if not SLUG_RE.match(slug or ""):
        raise ValueError(f"project name {slug!r}: lowercase letters, digits and dashes, up to 40")
    folder = project_dir(data_dir, slug)
    folder.mkdir(parents=True, exist_ok=True)
    with _locked(data_dir) as path:
        data = _read(path)
        card = data.get(slug) or {
            "name": name or title_for(slug), "status": "active",
            "priority": 1 + max([int(c.get("priority") or 0) for c in data.values() if isinstance(c, dict)] or [0]),
            "vertical": "", "summary": "", "current_state": "Just started.", "pm": None,
            "repo": None, "folder": str(folder), "team": [], "agents": [], "aliases": [],
        }
        if pm and not card.get("pm"):
            card["pm"] = pm
        for a in ([pm] if pm else []) + list(agents):
            if a and a not in card.setdefault("agents", []):
                card["agents"].append(a)
        card.setdefault("folder", str(folder))
        data[slug] = card
        _write(path, data)
    scaffold(folder, card.get("name") or title_for(slug), slug, card.get("pm"))
    return folder


def project_of(data_dir, seat):
    """The project a seat belongs to: the card listing it, else the one its name names (role-project)."""
    for slug, card in load(data_dir).items():
        if isinstance(card, dict) and (card.get("pm") == seat or seat in (card.get("agents") or [])):
            return slug
    return slug_for_seat(seat)
