"""RED-first: the Python interpreter of config/providers.json auth probes must
match api/src/routes/runtimes-available.ts semantics exactly (one probe
DEFINITION source; the same verdicts for the same inputs)."""
import json
from pathlib import Path

from orchestra_cli import runtime_probe as RP

REPO = Path(__file__).resolve().parents[2]


def _deps(*, which=(), cmd_out=None, cmd_fail=False, files=None, now_ms=1_000_000):
    files = files or {}

    def _which(name):
        return f"/usr/bin/{name}" if name in which else None

    def _run(argv):
        if cmd_fail:
            raise OSError("boom")
        return cmd_out if cmd_out is not None else ""

    def _read(path):
        if path in files:
            return files[path]
        raise FileNotFoundError(path)

    return RP.ProbeDeps(which=_which, run_cmd=_run, read_file=_read, now_ms=lambda: now_ms,
                        expand_home=lambda p: p.replace("~", "/home/x"))


def _prov(pid="claude", cli="claude", probe=None):
    return {"id": pid, "label": pid.title(), "cli": cli, "aliases": [],
            "detect": {"cmd": f"which {cli}"},
            "auth_probe": probe or {"kind": "cli-json", "cmd": "claude auth status",
                                    "success_key": "loggedIn"}}


def test_not_installed_is_loud_false_not_unverified():
    r = RP.probe_provider(_prov(), _deps(which=()))
    assert r["installed"] is False
    assert r["authed"] is False and r["auth_reason"] == "not-installed"


def test_cli_json_logged_in_true():
    r = RP.probe_provider(_prov(), _deps(which=("claude",), cmd_out='{"loggedIn": true}'))
    assert r["installed"] is True and r["authed"] is True and r["auth_reason"] is None


def test_cli_json_logged_in_false_carries_reason():
    r = RP.probe_provider(_prov(), _deps(which=("claude",), cmd_out='{"loggedIn": false}'))
    assert r["authed"] is False and r["auth_reason"] == "loggedIn=false"


def test_cli_json_cmd_failure_uses_fallback_file_probe():
    probe = {"kind": "cli-json", "cmd": "claude auth status", "success_key": "loggedIn",
             "fallback": {"kind": "file-json-key", "path": "~/.claude.json", "key": "oauthAccount"}}
    files = {"/home/x/.claude.json": json.dumps({"oauthAccount": {"email": "a@b"}})}
    r = RP.probe_provider(_prov(probe=probe), _deps(which=("claude",), cmd_fail=True, files=files))
    assert r["authed"] is True


def test_cli_json_cmd_failure_without_fallback_is_unverified():
    r = RP.probe_provider(_prov(), _deps(which=("claude",), cmd_fail=True))
    assert r["authed"] == "unverified" and r["auth_reason"] == "auth-probe-cmd-failed"


def test_cli_json_missing_key_is_unverified():
    r = RP.probe_provider(_prov(), _deps(which=("claude",), cmd_out='{"other": 1}'))
    assert r["authed"] == "unverified" and r["auth_reason"] == "auth-probe-missing-key:loggedIn"


def test_file_json_key_missing_file_and_missing_key():
    probe = {"kind": "file-json-key", "path": "~/.codex/auth.json", "key": "tokens"}
    r = RP.probe_provider(_prov("codex", "codex", probe), _deps(which=("codex",)))
    assert r["authed"] is False and r["auth_reason"] == "auth-file-missing"
    files = {"/home/x/.codex/auth.json": json.dumps({"nope": 1})}
    r = RP.probe_provider(_prov("codex", "codex", probe), _deps(which=("codex",), files=files))
    assert r["authed"] is False and r["auth_reason"] == "auth-file-missing-key:tokens"
    files = {"/home/x/.codex/auth.json": json.dumps({"tokens": {"access": "x"}})}
    r = RP.probe_provider(_prov("codex", "codex", probe), _deps(which=("codex",), files=files))
    assert r["authed"] is True


def test_file_json_expiry_future_past_and_unparseable():
    probe = {"kind": "file-json-expiry", "path": "~/.gemini/tok", "expiry_key": "token.expiry"}
    now_ms = 1_700_000_000_000  # 2023-11-14T22:13:20Z
    future = {"/home/x/.gemini/tok": json.dumps({"token": {"expiry": "2099-01-01T00:00:00Z"}})}
    past = {"/home/x/.gemini/tok": json.dumps({"token": {"expiry": "2001-01-01T00:00:00Z"}})}
    bad = {"/home/x/.gemini/tok": json.dumps({"token": {"expiry": "yesterday-ish"}})}
    none = {"/home/x/.gemini/tok": json.dumps({"token": {}})}
    d = lambda f: _deps(which=("agy",), files=f, now_ms=now_ms)  # noqa: E731
    assert RP.probe_provider(_prov("gemini", "agy", probe), d(future))["authed"] is True
    r = RP.probe_provider(_prov("gemini", "agy", probe), d(past))
    assert r["authed"] is False and r["auth_reason"] == "token-expired"
    r = RP.probe_provider(_prov("gemini", "agy", probe), d(bad))
    assert r["authed"] == "unverified" and r["auth_reason"] == "auth-file-unparseable-expiry"
    r = RP.probe_provider(_prov("gemini", "agy", probe), d(none))
    assert r["authed"] == "unverified" and r["auth_reason"] == "auth-file-missing-expiry:token.expiry"


def test_unknown_probe_kind_is_unverified():
    r = RP.probe_provider(_prov(probe={"kind": "magic"}), _deps(which=("claude",)))
    assert r["authed"] == "unverified" and r["auth_reason"] == "unknown-probe-kind:magic"


def test_probe_all_filters_by_enabled_and_keeps_catalog_order():
    provs = [_prov("claude"), _prov("gemini", "agy"), _prov("codex", "codex")]
    out = RP.probe_all(provs, enabled=["codex", "claude"], deps=_deps(which=("claude",), cmd_out='{"loggedIn": true}'))
    assert [p["id"] for p in out] == ["claude", "codex"]


def test_real_catalog_parses_and_uses_only_known_probe_kinds():
    provs = RP.load_providers(REPO / "config" / "providers.json")
    assert {p["id"] for p in provs} >= {"claude", "gemini", "codex"}
    for p in provs:
        probe = p["auth_probe"]
        while probe:
            assert probe["kind"] in RP.KNOWN_PROBE_KINDS
            probe = probe.get("fallback")
