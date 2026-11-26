"""The ported recall core and fact bus find their dirs from settings, never from a home-dir path."""
import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "brain"))

import bus  # noqa: E402
from services.recall import core  # noqa: E402


def test_bus_db_follows_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    try:
        assert importlib.reload(bus).DEFAULT_DB == os.path.join(str(tmp_path), "state", "brain", "facts.db")
    finally:
        monkeypatch.delenv("ORCHESTRA_DIR")
        importlib.reload(bus)


def test_bus_redactor_sits_next_to_the_bus():
    assert bus._LIVE_REDACTOR == os.path.join(_ROOT, "scripts", "brain", "redact_secrets.py")


def test_recall_code_dir_follows_its_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_SCRIPTS_DIR", str(tmp_path))
    try:
        assert str(importlib.reload(core).SCRIPTS_DIR) == str(tmp_path)
    finally:
        monkeypatch.delenv("RECALL_SCRIPTS_DIR")
        assert str(importlib.reload(core).SCRIPTS_DIR) == os.path.join(_ROOT, "scripts")
