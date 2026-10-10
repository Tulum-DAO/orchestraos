"""The audit's four "intent unclear" paths, decided (data-dir sweep follow-up).

- skills/: DATA (installed content; nothing in the checkout ships it). API side: routes/skills.ts.
- roadmaps: ONE file for reader and writer (api routes/roadmaps.test.ts).
- FLEET_AUDIT_*.md: DATA, generated into the data dir's docs/. The importer's env-less default was the
  CHECKOUT; it is now the one data-dir default.
- checkpoint RUNTIME line: the CODE version the seat runs, so the checkout's git, not the data dir's repo.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_focus_importer_default_is_the_data_dir_not_the_checkout(tmp_path):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[data]\ndir = "%s"\n' % (tmp_path / "data"))
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "ORCHESTRA_DIR", "ORCH_DIR", "ORCHESTRA_ROOT")}
    env["ORCHESTRA_CONFIG"] = str(cfg)
    r = subprocess.run([sys.executable, "-c",
                        f"import sys; sys.path.insert(0, {str(ROOT)!r});"
                        "from scripts.focus_registry import importer as I; print(I._DEFAULT_DOCS)"],
                       cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1200:]
    assert r.stdout.strip() == str(tmp_path / "data" / "docs")


def test_checkpoint_runtime_line_is_the_checkouts_git(monkeypatch, tmp_path):
    from scripts.continuity import checkpoint as C
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))          # a data dir that is not a git repo at all
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    assert C._runtime_harvest().get("head") == head
