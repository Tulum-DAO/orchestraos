"""RED-first (issue #104): nothing tells a user they are behind. `orchestra_cli.version` is
pure over an injected git runner so it runs hermetically: installed = `git describe --tags
--always`, latest = the highest v-semver tag in `git ls-remote --tags origin`; any git failure
is UNKNOWN, never an error — a notice must never block `up` or redden `doctor` on its own."""
import pytest

from orchestra_cli import version as V

LS = ("abc\trefs/tags/v0.1.0-hackathon\n"
      "def\trefs/tags/v0.2.0\n"
      "ghi\trefs/tags/v0.2.0^{}\n"
      "jkl\trefs/tags/v0.10.1\n"
      "mno\trefs/tags/not-a-version\n")


def _git(describe="v0.2.0", ls=LS, fail=()):
    def run(argv, cwd=None):
        if "describe" in argv:
            if "describe" in fail:
                return 128, "fatal: no names found"
            return 0, describe + "\n"
        if "ls-remote" in argv:
            if "ls-remote" in fail:
                return 128, "fatal: unable to access"
            return 0, ls
        return 1, "unexpected"
    return run


def test_installed_is_git_describe():
    assert V.installed("/repo", git=_git(describe="v0.2.0-3-gabc")) == "v0.2.0-3-gabc"


def test_latest_is_the_highest_semver_tag_ignoring_peeled_and_junk():
    assert V.latest_tag("/repo", git=_git()) == "v0.10.1"     # 0.10 > 0.2, ^{} peeled ignored


def test_parse_version_orders_numerically_and_ignores_suffixes():
    assert V.parse_version("v0.10.1") > V.parse_version("v0.2.0")
    assert V.parse_version("v0.1.0-hackathon") == (0, 1, 0)
    assert V.parse_version("v0.2.0-3-gabc") == (0, 2, 0)
    assert V.parse_version("gabc123") is None


def test_status_behind_when_installed_tag_is_lower():
    s = V.status("/repo", git=_git(describe="v0.2.0"))
    assert s == {"installed": "v0.2.0", "latest": "v0.10.1", "behind": True}


def test_status_current_when_installed_matches_latest():
    s = V.status("/repo", git=_git(describe="v0.10.1-2-gabc"))
    assert s["behind"] is False and s["latest"] == "v0.10.1"


def test_status_unknown_never_raises_when_git_fails():
    s = V.status("/repo", git=_git(fail=("ls-remote",)))
    assert s == {"installed": "v0.2.0", "latest": None, "behind": None}
    s = V.status("/repo", git=_git(fail=("describe", "ls-remote")))
    assert s["installed"] is None and s["behind"] is None


def test_notice_line_only_when_behind():
    assert "v0.10.1" in V.notice_line({"installed": "v0.2.0", "latest": "v0.10.1", "behind": True})
    assert "orchestra upgrade" in V.notice_line({"installed": "v0.2.0", "latest": "v0.10.1", "behind": True})
    assert V.notice_line({"installed": "v0.10.1", "latest": "v0.10.1", "behind": False}) == ""
    assert V.notice_line({"installed": None, "latest": None, "behind": None}) == ""
