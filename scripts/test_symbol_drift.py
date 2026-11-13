"""symbol_drift: two trees agree symbol by symbol, or the check fails (unless the drift is allowed
with a reason). Docstrings, comments and formatting never count; logic does."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import symbol_drift as D  # noqa: E402

BASE = '''
def f(x):
    """doc A"""
    # a comment
    return x + 1


class K:
    def m(self):
        return 1
'''


def _trees(tmp_path, a_src, b_src, spec, b_name="mod.py"):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "mod.py").write_text(a_src)
    (b / b_name).write_text(b_src)
    sp = tmp_path / "spec.json"
    sp.write_text(json.dumps(spec))
    return str(sp), str(a), str(b)


def _run(capsys, *args):
    rc = D.main(list(args))
    return rc, capsys.readouterr().out


def test_docstrings_comments_and_formatting_are_not_drift(tmp_path, capsys):
    b_src = BASE.replace('"""doc A"""', '"""a different doc"""').replace("# a comment", "").replace("x + 1", "x+1")
    sp, a, b = _trees(tmp_path, BASE, b_src, {"symbols": [{"file": "mod.py", "name": "f"}]})
    rc, out = _run(capsys, sp, "--a", a, "--b", b)
    assert rc == 0 and "EQUAL    mod.py::f" in out


def test_a_logic_change_is_drift_and_fails(tmp_path, capsys):
    sp, a, b = _trees(tmp_path, BASE, BASE.replace("x + 1", "x + 2"),
                      {"symbols": [{"file": "mod.py", "name": "f"}]})
    rc, out = _run(capsys, sp, "--a", a, "--b", b, "--diff")
    assert rc == 1 and "DRIFT    DIFF" in out and "x + 2" in out


def test_a_method_is_compared_by_dotted_name(tmp_path, capsys):
    sp, a, b = _trees(tmp_path, BASE, BASE.replace("return 1", "return 2"),
                      {"symbols": [{"file": "mod.py", "name": "K.m"}]})
    rc, out = _run(capsys, sp, "--a", a, "--b", b)
    assert rc == 1 and "DRIFT    DIFF      mod.py::K.m" in out        # found and different, not MISSING


def test_a_missing_symbol_is_drift(tmp_path, capsys):
    sp, a, b = _trees(tmp_path, BASE, "def g():\n    pass\n", {"symbols": [{"file": "mod.py", "name": "f"}]})
    rc, out = _run(capsys, sp, "--a", a, "--b", b)
    assert rc == 1 and "MISSING-B" in out


def test_allowed_drift_passes_with_its_reason_and_a_stale_allow_is_flagged(tmp_path, capsys):
    spec = {"symbols": [{"file": "mod.py", "name": "f"}, {"file": "mod.py", "name": "K.m"}],
            "allow": {"mod.py::f": "tree B keeps its own reader", "mod.py::K.m": "was different once"}}
    sp, a, b = _trees(tmp_path, BASE, BASE.replace("x + 1", "x + 2"), spec)
    rc, out = _run(capsys, sp, "--a", a, "--b", b)
    assert rc == 0
    assert "ALLOWED  DIFF" in out and "tree B keeps its own reader" in out
    assert "STALE" in out                                      # K.m matches: the allowance is dead


def test_b_file_maps_a_renamed_file(tmp_path, capsys):
    sp, a, b = _trees(tmp_path, BASE, BASE, {"symbols": [{"file": "mod.py", "b_file": "other.py", "name": "f"}]},
                      b_name="other.py")
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 0


@pytest.mark.parametrize("bad", ["not json", json.dumps({"symbols": [{"file": "nope.py", "name": "f"}]})])
def test_an_unreadable_spec_or_file_is_exit_2_not_a_pass(tmp_path, capsys, bad):
    """A gate must not pass on absent evidence."""
    sp, a, b = _trees(tmp_path, BASE, BASE, {})
    Path(sp).write_text(bad)
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 2


# ---------------------------------------------------------------- review R1 (no gate may pass on absent evidence)

@pytest.mark.parametrize("key", ["file", "b_file"])
def test_an_absolute_or_escaping_path_is_refused_not_compared_with_itself(tmp_path, capsys, key):
    outside = tmp_path / "outside.py"
    outside.write_text(BASE)
    sym = {"file": "mod.py", "name": "f"}
    sym[key] = str(outside)
    sp, a, b = _trees(tmp_path, BASE, BASE.replace("x + 1", "x + 2"), {"symbols": [sym]})
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 2
    sym[key] = "../outside.py"
    Path(sp).write_text(json.dumps({"symbols": [sym]}))
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 2


@pytest.mark.parametrize("spec", [{}, {"symbol": [{"file": "mod.py", "name": "f"}]}, {"symbols": []},
                                  [], {"symbols": {"file": "mod.py", "name": "f"}}, {"symbols": [["mod.py", "f"]]}])
def test_an_empty_or_malformed_spec_is_exit_2(tmp_path, capsys, spec):
    sp, a, b = _trees(tmp_path, BASE, BASE, spec)
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 2


def test_a_redefined_symbol_compares_every_definition(tmp_path, capsys):
    """Python runs the LAST def; a property setter shares its getter's name. All of them count."""
    a_src = BASE + "\ndef f(x):\n    return 2\n"
    sp, a, b = _trees(tmp_path, a_src, a_src.replace("return 2", "return 3"),
                      {"symbols": [{"file": "mod.py", "name": "f"}]})
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 1
    prop = "class P:\n    @property\n    def x(self):\n        return self._x\n\n    @x.setter\n    def x(self, v):\n        self._x = v\n"
    (tmp_path / "p").mkdir()
    sp, a, b = _trees(tmp_path / "p", prop, prop.replace("self._x = v", "self._x = -v"),
                      {"symbols": [{"file": "mod.py", "name": "P.x"}]})
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 1


def test_an_allow_for_no_listed_symbol_is_flagged_and_an_allow_never_covers_missing(tmp_path, capsys):
    spec = {"symbols": [{"file": "mod.py", "name": "f"}],
            "allow": {"mod.py::f": "B tweaks the return", "mod.py::gone": "typo or removed"}}
    sp, a, b = _trees(tmp_path, BASE, "def g():\n    pass\n", spec)
    rc, out = _run(capsys, sp, "--a", a, "--b", b)
    assert rc == 1 and "DRIFT    MISSING-B" in out          # an allow is for a DIFF, never a vanished symbol
    assert "mod.py::gone" in out and "UNUSED" in out


def test_the_same_tree_twice_is_refused(tmp_path, capsys):
    sp, a, _ = _trees(tmp_path, BASE, BASE, {"symbols": [{"file": "mod.py", "name": "f"}]})
    assert _run(capsys, sp, "--a", a, "--b", a)[0] == 2


def test_the_diff_shows_every_statement_after_the_docstring(tmp_path, capsys):
    a_src = 'def f():\n    "doc"\n    "keep"\n    return 1\n'
    sp, a, b = _trees(tmp_path, a_src, a_src.replace("return 1", "return 2"),
                      {"symbols": [{"file": "mod.py", "name": "f"}]})
    rc, out = _run(capsys, sp, "--a", a, "--b", b, "--diff")
    assert rc == 1 and "keep" in out              # the 2nd leading string survives (one strip, on a copy)


@pytest.mark.parametrize("change", [("def f(x):", "@staticmethod\ndef f(x):"), ("def f(x):", "def f(x=1):"),
                                    ("def f(x):", "def f(x: int):"), ("def f(x):", "async def f(x):")])
def test_signature_level_changes_are_drift(tmp_path, capsys, change):
    sp, a, b = _trees(tmp_path, BASE, BASE.replace(*change), {"symbols": [{"file": "mod.py", "name": "f"}]})
    assert _run(capsys, sp, "--a", a, "--b", b)[0] == 1
