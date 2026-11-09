"""Tests for red_alert_issue scrub + fingerprint + preview gating."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import red_alert_issue as RAI  # noqa: E402


def test_scrub_ips_and_homes():
    s = RAI.scrub("host 100.68.171.99 path /home/shaw/scripts/x and 10.0.0.5")
    assert "100.68" not in s
    assert "/home/shaw" not in s
    assert "<tailscale-ip>" in s
    assert "<home>" in s
    assert "<private-ip>" in s


def test_scrub_secrets_and_tailnet():
    s = RAI.scrub("token: ghp_abcdefghijklmnopqrstuvwxyz012345 orchestra-vps.ts.net")
    assert "ghp_" not in s
    assert "orchestra-vps" not in s
    assert "<secret>" in s or "<redacted>" in s


def test_scrub_names_from_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_SCRUB_NAMES", "AcmeCorp,Jane Doe")
    s = RAI.scrub("AcmeCorp hired Jane Doe")
    assert "AcmeCorp" not in s
    assert "Jane Doe" not in s


def test_fingerprint_stable():
    r = {"seats": ["gm"], "class": "pane_dead", "symptom": "dead at /home/shaw/x"}
    a = RAI.fingerprint(r)
    b = RAI.fingerprint(r)
    assert a == b
    assert len(a) == 16


def test_preview_blocks_without_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRA_GITHUB_ISSUES_REPO", raising=False)
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "missing.toml"))
    prev = RAI.build_preview({
        "id": "ra_test", "seats": ["a"], "class": "api_error",
        "symptom": "API Error on 100.1.2.3", "status": "open", "evidence": {},
    })
    assert prev["repo_configured"] is False
    assert prev["can_file"] is False
    assert "100.1" not in prev["body"]
    assert "orchestraos-red-alert-fp:" in prev["body"]


def test_dry_run_create_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_GITHUB_ISSUES_REPO", "example/private-test")
    monkeypatch.setenv("GH_TOKEN", "x")  # auth_ok via token
    prev = RAI.build_preview({
        "id": "ra_test2", "seats": ["b"], "class": "login_screen",
        "symptom": "Please log in", "status": "open",
    })
    assert prev["can_file"] is True
    # find_open_issue will fail soft without real gh — dry_run still returns shape
    monkeypatch.setattr(RAI, "find_open_issue", lambda repo, fp: None)
    out = RAI.create_or_comment(prev, dry_run=True)
    assert out["ok"] and out["dry_run"] and out["action"] == "create"
