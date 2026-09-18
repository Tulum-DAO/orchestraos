"""RED-first — semantic_memory is an OPTIONAL extension (gm follow-up on PR #1).

The public tree ships services/arturo/semantic_recall.py but not the
scripts/semantic_memory library it consumes. On a clean install the module must
import, and a recall call must log 'semantic recall unavailable' ONCE and return
'' — never raise into the turn, never spam the log per turn. The extension point
(drop a `semantic_memory` package under <repo>/scripts/) is documented in
docs/MEMORY.md.
"""
import logging
import sys

import pytest

from services.arturo import semantic_recall as sr


@pytest.fixture(autouse=True)
def _no_library(monkeypatch, tmp_path):
    """Hermetic: whatever the host has, the library is NOT importable here."""
    sr._reset_for_tests()
    monkeypatch.setitem(sys.modules, "semantic_memory", None)
    monkeypatch.setitem(sys.modules, "semantic_memory.query", None)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("ARTURO_SEMANTIC_RECALL", "1")
    yield
    sr._reset_for_tests()


def _unavailable_lines(caplog):
    return [r for r in caplog.records if "semantic recall unavailable" in r.message]


def test_module_imports_without_the_library():
    assert sr.recall_preamble  # import already succeeded at module load


def test_available_is_false_without_library():
    assert sr.available() is False


def test_recall_returns_empty_and_warns_exactly_once(caplog):
    with caplog.at_level(logging.INFO, logger=sr.log.name):
        first = sr.recall_preamble("what is going on with the acme dental pixel rollout")
        second = sr.recall_preamble("and what about the listmagic server migration")
    assert first == "" and second == ""
    lines = _unavailable_lines(caplog)
    assert len(lines) == 1, [r.message for r in caplog.records]
    assert lines[0].levelno <= logging.WARNING
    assert "docs/MEMORY.md" in lines[0].message


def test_unavailable_even_when_a_db_file_exists(tmp_path, caplog):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "semantic-memory.db").write_bytes(b"")
    with caplog.at_level(logging.INFO, logger=sr.log.name):
        assert sr.recall_preamble("what is going on with the acme dental pixel rollout") == ""
    assert len(_unavailable_lines(caplog)) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_flag_off_never_touches_the_library(monkeypatch, caplog):
    monkeypatch.delenv("ARTURO_SEMANTIC_RECALL", raising=False)
    with caplog.at_level(logging.DEBUG, logger=sr.log.name):
        assert sr.recall_preamble("what is going on with the acme dental pixel rollout") == ""
    assert caplog.records == []


def test_library_search_path_is_the_code_root_not_the_data_dir(tmp_path):
    # the extension point lives beside the code (<repo>/scripts/semantic_memory);
    # with the code/data split the data dir has no scripts/ at all
    assert sr.library_dir().endswith("/scripts")
    assert not sr.library_dir().startswith(str(tmp_path))
