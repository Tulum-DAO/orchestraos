"""RED-first: `orchestra upgrade` = show what changed, git pull --ff-only, init --yes, doctor.
Seats are never touched; a dirty checkout or a diverged branch refuses before pulling."""
from pathlib import Path

from orchestra_cli import upgrade_cmd as U


class Git:
    """Fake git: scripted outputs per subcommand; records every call."""

    def __init__(self, *, incoming=("abc1234 fix: x", "def5678 feat: y"), changed=("scripts/foo.py",),
                 dirty="", pull_rc=0):
        self.calls = []
        self.incoming = list(incoming)
        self.changed = list(changed)
        self.dirty = dirty
        self.pull_rc = pull_rc

    def __call__(self, argv, cwd=None):
        self.calls.append(tuple(argv))
        a = list(argv)
        if a[:2] == ["git", "fetch"]:
            return 0, ""
        if a[:2] == ["git", "status"]:
            return 0, self.dirty
        if a[:2] == ["git", "log"]:
            return 0, "\n".join(self.incoming)
        if a[:2] == ["git", "diff"]:
            return 0, "\n".join(self.changed)
        if a[:2] == ["git", "pull"]:
            return self.pull_rc, "" if self.pull_rc == 0 else "fatal: Not possible to fast-forward, aborting."
        if a[:2] == ["git", "rev-parse"]:
            return 0, "abc1234"
        return 0, ""


def _run(tmp_path, git, **kw):
    inits, doctors = [], []
    rep = U.run_upgrade(tmp_path, git=git,
                        init=lambda root, **k: inits.append(k) or [],
                        doctor=lambda root: doctors.append(root) or 0, **kw)
    return rep, inits, doctors


def test_upgrade_happy_path_pulls_ff_only_then_init_yes_then_doctor(tmp_path):
    git = Git()
    rep, inits, doctors = _run(tmp_path, git)
    assert rep.ok and rep.exit_code == 0
    assert ("git", "pull", "--ff-only") in [c[:3] for c in git.calls]
    assert inits == [{"yes": True}] and doctors == [tmp_path]
    text = rep.render()
    assert "2 commit(s)" in text and "abc1234 fix: x" in text
    assert "seats" in text.lower() and "untouched" in text.lower()
    assert "orchestra down" in text and "orchestra up" in text


def test_upgrade_flags_contract_bearing_paths(tmp_path):
    git = Git(changed=("msg_store.py", "scripts/lineage_daemon/bg_beat.py", "scripts/approval_resume.py", "README.md"))
    rep, _, _ = _run(tmp_path, git)
    assert set(rep.contract_paths) == {"msg_store.py", "scripts/lineage_daemon/bg_beat.py", "scripts/approval_resume.py"}
    assert "contract-bearing" in rep.render()


def test_upgrade_nothing_to_pull_still_runs_init_and_doctor(tmp_path):
    rep, inits, doctors = _run(tmp_path, Git(incoming=(), changed=()))
    assert rep.ok and "already up to date" in rep.render()
    assert inits and doctors


def test_upgrade_refuses_a_dirty_checkout_before_pulling(tmp_path):
    git = Git(dirty=" M scripts/foo.py\n")
    rep, inits, doctors = _run(tmp_path, git)
    assert not rep.ok and rep.exit_code == 2
    assert not any(c[:2] == ("git", "pull") for c in git.calls)
    assert inits == [] and doctors == []
    assert "uncommitted" in rep.render()


def test_upgrade_reports_a_failed_fast_forward_and_stops(tmp_path):
    git = Git(pull_rc=1)
    rep, inits, doctors = _run(tmp_path, git)
    assert not rep.ok and rep.exit_code == 1
    assert inits == [] and doctors == []
    assert "fast-forward" in rep.render()


def test_upgrade_dry_run_only_shows_what_would_change(tmp_path):
    git = Git()
    rep, inits, doctors = _run(tmp_path, git, dry_run=True)
    assert rep.ok and inits == [] and doctors == []
    assert not any(c[:2] == ("git", "pull") for c in git.calls)
    assert "dry run" in rep.render().lower()


def test_upgrade_passes_skip_flags_to_init(tmp_path):
    rep, inits, _ = _run(tmp_path, Git(), init_kwargs={"skip_npm": True, "skip_venv": True, "skip_build": True})
    assert inits == [{"yes": True, "skip_npm": True, "skip_venv": True, "skip_build": True}]


def test_cli_parses_upgrade():
    from orchestra_cli import __main__ as M
    ns = M.parse_args(["upgrade", "--dry-run"])
    assert ns.command == "upgrade" and ns.dry_run is True
