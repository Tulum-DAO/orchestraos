"""Tests for red_alert_issue scrub + fingerprint + preview gating + prefilled URL."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import red_alert_issue as RAI  # noqa: E402


def test_scrub_ips_and_homes():
    s = RAI.scrub("host 100.68.171.99 path /home/operator/scripts/x and 10.0.0.5")
    assert "100.68" not in s
    assert ("/home/"+"shaw") not in s
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
    r = {"seats": ["gm"], "class": "pane_dead", "symptom": "dead at /home/operator/x"}
    a = RAI.fingerprint(r)
    b = RAI.fingerprint(r)
    assert a == b
    assert len(a) == 16


def test_preview_defaults_to_public_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRA_GITHUB_ISSUES_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(RAI, "_gh_auth_ok", lambda: (False, "neither gh nor token"))
    prev = RAI.build_preview({
        "id": "ra_test", "seats": ["a"], "class": "api_error",
        "symptom": "API Error on 100.1.2.3", "status": "open", "evidence": {},
    })
    assert prev["repo"] == "Tulum-DAO/orchestraos"
    assert prev["repo_configured"] is True
    assert prev["can_file"] is False
    assert prev["can_open_url"] is True
    assert "100.1" not in prev["body"]
    assert "orchestraos-red-alert-fp:" in prev["body"]
    u = urlparse(prev["new_issue_url"])
    assert u.netloc == "github.com"
    assert u.path == "/Tulum-DAO/orchestraos/issues/new"
    q = parse_qs(u.query)
    assert q["labels"] == ["red-alert"]
    assert "100.1" not in q["body"][0]
    assert "API Error" in q["title"][0]


def test_repo_override_from_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_GITHUB_ISSUES_REPO", "example/private-test")
    prev = RAI.build_preview({
        "id": "ra_ov", "seats": ["a"], "class": "bug", "symptom": "x", "status": "open",
    })
    assert prev["repo"] == "example/private-test"
    assert "/example/private-test/issues/new" in prev["new_issue_url"]


def test_open_url_without_auth_does_not_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRA_GITHUB_ISSUES_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(RAI, "_gh_auth_ok", lambda: (False, "neither gh nor token"))
    prev = RAI.build_preview({
        "id": "ra_url", "seats": ["b"], "class": "login_screen",
        "symptom": "Please log in at /home/operator/x", "status": "open",
    })
    out = RAI.create_or_comment(prev, dry_run=True)
    assert out["ok"] and out["action"] == "open_url" and out["dry_run"] is True
    assert "Tulum-DAO/orchestraos/issues/new" in out["url"]
    assert "/home/operator" not in out["url"]
    out2 = RAI.create_or_comment(prev, dry_run=False)
    assert out2["ok"] and out2["action"] == "open_url"
    assert out2.get("dry_run") is False


def test_dry_run_create_shape(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_GITHUB_ISSUES_REPO", "example/private-test")
    monkeypatch.setenv("GH_TOKEN", "x")  # auth_ok via token
    prev = RAI.build_preview({
        "id": "ra_test2", "seats": ["b"], "class": "login_screen",
        "symptom": "Please log in", "status": "open",
    })
    assert prev["can_file"] is True
    monkeypatch.setattr(RAI, "find_open_issue", lambda repo, fp: None)
    out = RAI.create_or_comment(prev, dry_run=True)
    assert out["ok"] and out["dry_run"] and out["action"] == "create"


def test_token_only_create_uses_api(monkeypatch):
    """GITHUB_TOKEN without gh files via the REST API (mocked; never hits the network)."""
    monkeypatch.setenv("ORCHESTRA_GITHUB_ISSUES_REPO", "example/private-test")
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setattr(RAI, "find_open_issue", lambda repo, fp: None)
    monkeypatch.setattr(RAI.shutil, "which", lambda name: None)
    calls = []

    def fake_api(method, path, token, payload=None):
        calls.append((method, path, payload))
        assert token == "x"
        return 201, {"number": 42, "html_url": "https://github.com/example/private-test/issues/42"}

    monkeypatch.setattr(RAI, "_github_api", fake_api)
    prev = RAI.build_preview({
        "id": "ra_tok", "seats": ["c"], "class": "bug", "symptom": "x", "status": "open",
    })
    assert prev["can_file"] is True
    out = RAI.create_or_comment(prev, dry_run=False)
    assert out["ok"] and out["action"] == "create" and out["number"] == 42
    assert calls and calls[0][0] == "POST" and calls[0][1] == "/repos/example/private-test/issues"
    assert calls[0][2]["labels"] == ["red-alert"]
    assert "orchestraos-red-alert-fp:" in calls[0][2]["body"]
