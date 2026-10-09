"""GET /agent-file: one file from an allowlisted agent's working folder, behind every fence gm ruled.

Route tests run the REAL app (scope middleware, real DeviceStore, real config file); the module tests
hit each fence directly. Every sabotage of a fence turns a named test here red."""
import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agent_file as AF  # noqa: E402
import scripts.watch_gateway as G  # noqa: E402
from scripts.device_tokens import DeviceStore  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"


@pytest.fixture
def box(tmp_path, monkeypatch):
    """home/, data/ (registry + devices), work/repo (the seat's cwd), outside/ (must never be served)."""
    home, data, work, outside = (tmp_path / n for n in ("home", "data", "work", "outside"))
    repo = work / "repo"
    for d in (home, data / "state", repo / "src", outside):
        d.mkdir(parents=True)
    (repo / "README.md").write_text("# hello\n")
    (repo / "src" / "app.py").write_text("print('hi')\n")
    (outside / "private.txt").write_text("OUTSIDE")
    (data / "registry.json").write_text(json.dumps({"agents": {"dev": {"cwd": str(repo)}}}))
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(f'[code]\nroots = ["{work}"]\n')
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    monkeypatch.setattr(G, "_agent_file_hits", {})
    G._code_roots_cache.update(path=None, mtime=None, roots=[])
    store = DeviceStore(data / "state" / "devices")
    dev_id, dev_tok = store.mint("mac", ["read", "code"])
    _, read_only = store.mint("phone", ["read"])
    return {"tmp": tmp_path, "home": home, "data": data, "repo": repo, "outside": outside, "cfg": cfg,
            "store": store, "dev_id": dev_id, "code": dev_tok, "read": read_only}


def _get(box, path, token="code", seat="dev", method="GET", headers=None, raw_query=None):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            h = dict(headers or {})
            if token:
                h["Authorization"] = f"Bearer {box[token] if token in box else token}"
            q = raw_query if raw_query is not None else {"seat": seat, "path": path}
            r = await c.request(method, "/agent-file", params=q, headers=h)
            return r.status, r.headers.copy(), await r.read()
    return asyncio.run(go())


def _audit(box):
    p = box["data"] / "logs" / "agent-file-audit.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# ---- the happy path ------------------------------------------------------------------------------

def test_a_file_in_the_seats_folder_is_served_with_safe_headers(box):
    st, h, body = _get(box, "src/app.py")
    assert st == 200 and body == b"print('hi')\n"
    assert h["Content-Type"] == "text/plain; charset=utf-8"
    assert h["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert h["X-Content-Type-Options"] == "nosniff" and h["Cache-Control"] == "private, no-store"


def test_head_answers_without_a_body(box):
    st, h, body = _get(box, "README.md", method="HEAD")
    assert st == 200 and body == b"" and int(h["Content-Length"]) == len(b"# hello\n")


def test_binary_is_an_attachment(box):
    (box["repo"] / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    st, h, _ = _get(box, "blob.bin")
    assert st == 200 and h["Content-Type"] == "application/octet-stream"
    assert h["Content-Disposition"].startswith("attachment")


# ---- 2. path rules ---------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["../outside/private.txt", "src/../../outside/private.txt", "/etc/passwd",
                                  "src//app.py", "./README.md", "src\\app.py", "a\x00b", "",
                                  "C:/Windows/win.ini", "x/" * 40 + "y", "a" * 1100])
def test_traversal_and_malformed_paths_are_refused(box, path):
    st, h, body = _get(box, path)
    assert st == 404 and b"OUTSIDE" not in body
    assert h["Content-Security-Policy"] == "default-src 'none'; sandbox"


def test_percent_encoded_dotdot_is_decoded_then_refused(box):
    st, _, body = _get(box, None, raw_query="seat=dev&path=%2e%2e%2foutside%2fprivate.txt")
    assert st == 404 and b"OUTSIDE" not in body


# ---- 3. confinement through symlinks ---------------------------------------------------------------

def test_a_file_symlink_pointing_outside_is_refused(box):
    (box["repo"] / "innocent.txt").symlink_to(box["outside"] / "private.txt")
    st, _, body = _get(box, "innocent.txt")
    assert st == 404 and b"OUTSIDE" not in body


def test_a_directory_symlink_pointing_outside_is_refused(box):
    (box["repo"] / "docs").symlink_to(box["outside"])
    st, _, body = _get(box, "docs/private.txt")
    assert st == 404 and b"OUTSIDE" not in body


def test_an_innocent_name_linking_to_a_denied_file_is_refused(box):
    (box["repo"] / ".env").write_text("A=1")
    (box["repo"] / "notes.txt").symlink_to(box["repo"] / ".env")
    assert _get(box, "notes.txt")[0] == 404


def test_a_parent_swapped_for_a_symlink_between_check_and_open_is_refused(box):
    """gm (1): realpath and stat both follow the swap; only the descriptor's own path catches it."""
    root = box["repo"]
    (root / "sub").mkdir()
    (root / "sub" / "f.txt").write_text("inside")
    (box["outside"] / "f.txt").write_text("OUTSIDE")

    def swap():
        shutil.rmtree(root / "sub")
        (root / "sub").symlink_to(box["outside"])
    with pytest.raises(AF.Refused) as e:
        AF.read_file(root, "sub/f.txt", _after_check=swap)
    assert e.value.reason in ("outside-root", "open-failed")


# ---- 4. deny at any depth ----------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    ".env", "src/.env.local", ".git/config", "src/.ssh/id_rsa", ".claude/settings.json",
    "keys/server.pem", "deploy/tls.KEY", "a/b/id_ed25519", "config/client_secret.json", "CREDENTIALS.txt",
    "app/token.txt", "prod.env", "infra/main.tfstate", "vault.kdbx", "vpn/home.ovpn",
    "gcp/my-service-account.json", "gcp/deploy-key.json", "state/tasks.db", "data/app.sqlite",
    "run.log", "logs/today.txt", "backups/2026.tar", "settings.json.bak", "x.bak2", "transcript.jsonl",
    "a.secrets", "home/.npmrc", "Sub/PassWords.txt", "keys/server.ppk", "pub/key.asc", "x.gpg"])
def test_denied_names_are_refused_at_any_depth_and_any_case(box, rel):
    p = box["repo"] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    st, _, _ = _get(box, rel)
    assert st == 404
    assert AF.denied(rel.split("/")), rel


# ---- 7. secret-shaped content, inside innocently named files at depth --------------------------------

def _j(*parts):
    """Fixtures are assembled at run time so no secret-shaped literal sits in this file (the repo's
    own secret scanner, rightly, refuses to commit one). Every value below is fake."""
    return "".join(parts)


_B64 = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
_PEM = "-----"
SECRETS = {
    "private-key": _j(_PEM, "BEGIN OPENSSH ", "PRIVATE KEY", _PEM, "\nb3Blbn\n", _PEM, "END OPENSSH ", "PRIVATE KEY", _PEM),
    "rsa-private-key": _j(_PEM, "BEGIN RSA ", "PRIVATE KEY", _PEM, "\nMIIEow\n", _PEM, "END RSA ", "PRIVATE KEY", _PEM),
    "anthropic": _j("key = ", "sk-", "ant-", "api03-", _B64),
    "openai": _j("OPENAI=", "sk-", "proj-", _B64[:30]),
    "stripe-restricted": _j("rk", "_live_", _B64[:20]),
    "stripe-secret": _j("sk", "_live_", _B64[:20]),
    "slack": _j("xo", "xb-", "1234567890-", _B64[:16]),
    "github": _j("gh", "p_", _B64),
    "github-pat": _j("github", "_pat_", "11", _B64, "_", _B64[:20]),
    "aws": _j("aws_access_key_id = ", "AK", "IA", "Z" * 16),
    "gcp-sa": _j('{"type": "service_account", "private', '_key": "x"}'),
    "pgp-private": _j(_PEM, "BEGIN PGP ", "PRIVATE KEY", " BLOCK", _PEM, "\nlQOYBF\n"),
    "putty": _j("PuTTY-User-Key-File-", "3: ssh-ed25519\nEncryption: none\n"),
    "db-uri": _j("DATABASE_URL=postgres://", "app", ":", "s3cr3t-pw", "@db.internal:5432/app"),
    "jwt": _j("Authorization: Bearer ", "ey", "J", "hbGciOiJub25lIn0", ".", "ey", "J", "zdWIiOiJ4In0aaaa", ".", _B64[:20]),
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_secret_shaped_content_is_refused_whatever_the_name(box, kind):
    p = box["repo"] / "docs" / "notes" / "readme-2.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("harmless line\n" + SECRETS[kind] + "\nmore text\n")
    st, h, body = _get(box, "docs/notes/readme-2.md")
    assert st == 404 and b"harmless" not in body
    assert h["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert _audit(box)[-1]["reason"].startswith("secret-shaped:"), "the reason lives in the audit only"


def test_the_gateway_bearer_in_a_file_is_refused_and_never_logged(box):
    (box["repo"] / "debug.txt").write_text(f"curl -H 'Authorization: Bearer {FLEET}'\n")
    assert _get(box, "debug.txt")[0] == 404 and _audit(box)[-1]["reason"] == "secret-shaped:gateway-bearer"
    assert FLEET not in (box["data"] / "logs" / "agent-file-audit.jsonl").read_text()


def test_a_device_token_in_a_file_is_refused_by_hash_and_never_logged(box):
    (box["repo"] / "paste.txt").write_text(f"token was {box['read']} ok\n")
    assert _get(box, "paste.txt")[0] == 404
    audit = (box["data"] / "logs" / "agent-file-audit.jsonl").read_text()
    assert box["read"] not in audit and "device-token" in audit


def test_ordinary_urls_and_code_are_not_secret_shaped(box):
    (box["repo"] / "links.md").write_text("see https://example.com/a:b and git@github.com:org/repo.git\n"
                                          "postgres://localhost/app and http://host:8080/path\n")
    assert _get(box, "links.md")[0] == 200


def test_the_audit_log_rolls_over_instead_of_growing_forever(box, monkeypatch):
    monkeypatch.setattr(G, "AGENT_FILE_AUDIT_MAX_BYTES", 200)
    for _ in range(6):
        _get(box, "README.md")
    logs = box["data"] / "logs"
    assert (logs / "agent-file-audit.jsonl.1").exists()
    assert (logs / "agent-file-audit.jsonl").stat().st_size <= 200 + 400


def test_a_43_char_string_that_is_no_device_token_is_served(box):
    (box["repo"] / "hash.txt").write_text("commit " + "a" * 43 + "\n")
    assert _get(box, "hash.txt")[0] == 200


# ---- 1. roots -------------------------------------------------------------------------------------------

def test_no_roots_configured_means_off_and_not_advertised(box):
    box["cfg"].write_text("[data]\n")
    assert _get(box, "README.md")[0] == 404

    async def caps():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.get("/gateway/capabilities", headers={"Authorization": f"Bearer {box['code']}"})
            return (await r.json())["features"]
    assert "agent_file" not in asyncio.run(caps())
    box["cfg"].write_text(f'[code]\nroots = ["{box["repo"].parent}"]\n')
    assert "agent_file" in asyncio.run(caps())


def test_a_seat_outside_every_allowlisted_root_is_refused(box):
    other = box["tmp"] / "elsewhere" / "proj"
    other.mkdir(parents=True)
    (other / "a.txt").write_text("x")
    reg = json.loads((box["data"] / "registry.json").read_text())
    reg["agents"]["stray"] = {"cwd": str(other)}
    (box["data"] / "registry.json").write_text(json.dumps(reg))
    assert _get(box, "a.txt", seat="stray")[0] == 404


def test_an_unregistered_seat_is_refused(box):
    assert _get(box, "README.md", seat="nobody")[0] == 404


@pytest.mark.parametrize("which", ["home", "slash", "contains-home", "contains-data", "the-data-dir", "inside-data"])
def test_roots_that_are_too_wide_or_touch_the_data_dir_are_refused(box, which):
    home, data, tmp = box["home"], box["data"], box["tmp"]
    root = {"home": home, "slash": Path("/"), "contains-home": tmp, "contains-data": tmp,
            "the-data-dir": data, "inside-data": data / "state"}[which]
    with pytest.raises(AF.Refused):
        AF.check_root(root, home=home, data_dir=data)


def test_a_root_that_contains_home_but_not_the_data_dir_is_refused(tmp_path):
    """Isolates the home rule: the data dir lives elsewhere, so only `contains $HOME` can refuse."""
    users = tmp_path / "users"
    home = users / "me"
    data = tmp_path / "srv" / "data"
    home.mkdir(parents=True)
    data.mkdir(parents=True)
    with pytest.raises(AF.Refused) as e:
        AF.check_root(users, home=home, data_dir=data)
    assert e.value.reason == "root-contains-home"


# ---- 5/6. transport rules ------------------------------------------------------------------------------

def test_tailscale_funnel_requests_are_refused(box):
    st, _, _ = _get(box, "README.md", headers={"Tailscale-Funnel-Request": "?1"})
    assert st == 404 and _audit(box)[-1]["reason"] == "funnel"


def test_read_scope_is_not_enough_and_no_token_is_401(box):
    assert _get(box, "README.md", token="read")[0] == 403
    assert _get(box, "README.md", token=None)[0] == 401


def test_too_large_is_refused(box, monkeypatch):
    monkeypatch.setattr(AF, "MAX_BYTES", 10)
    (box["repo"] / "big.txt").write_text("x" * 11)
    with pytest.raises(AF.Refused) as e:
        AF.read_file(box["repo"], "big.txt", max_bytes=10)
    assert e.value.reason == "too-large"


def test_directories_and_fifos_are_not_regular_files(box):
    (box["repo"] / "adir").mkdir()
    os.mkfifo(box["repo"] / "pipe")
    assert _get(box, "adir")[0] == 404 and _get(box, "pipe")[0] == 404


def test_rate_limit_per_device(box, monkeypatch):
    monkeypatch.setattr(G, "AGENT_FILE_RATE_PER_MIN", 3)
    codes = [_get(box, "README.md")[0] for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_one_404_for_missing_denied_and_not_allowlisted_with_the_reason_only_in_the_audit(box):
    (box["repo"] / ".env").write_text("A=1")
    answers = [_get(box, "nope.txt"), _get(box, ".env"), _get(box, "README.md", seat="nobody")]
    assert {(st, body) for st, _, body in answers} == {(404, answers[0][2])}
    reasons = [r["reason"] for r in _audit(box)][-3:]
    assert reasons == ["open-failed", "deny-dot", "seat-not-registered"]


def test_the_audit_never_holds_file_bytes(box):
    (box["repo"] / "s.txt").write_text("PAYLOAD-THAT-MUST-NOT-BE-LOGGED")
    assert _get(box, "s.txt")[0] == 200
    line = _audit(box)[-1]
    assert "PAYLOAD" not in json.dumps(line)
    assert line["status"] == 200 and line["resolved"] == "s.txt" and line["device"] == box["dev_id"]


# ---- revocation -------------------------------------------------------------------------------------------

def test_a_revoked_device_gets_401_on_its_next_request(box):
    assert _get(box, "README.md")[0] == 200
    box["store"].revoke(box["dev_id"])
    assert _get(box, "README.md")[0] == 401


def test_rotating_the_fleet_token_revokes_a_fleet_minted_device(box):
    from orchestra_cli.pair_cmd import run_rotate_fleet_token
    _, tok = box["store"].mint("mac2", ["code"], minted_by=G.FLEET_MINTER_ID)
    box["minted"] = tok
    assert _get(box, "README.md", token="minted")[0] == 200

    class A:
        minted_by = None
        revoke_only = True
    run_rotate_fleet_token(A(), out=lambda *_: None)
    assert _get(box, "README.md", token="minted")[0] == 401


def test_code_is_not_mintable_over_http():
    from scripts.device_tokens import http_mintable
    assert http_mintable(["code"])[0] is False


# ---- gm rulings on #340 (msg_18064c14) ------------------------------------------------------------

def test_a_secret_shaped_file_and_a_missing_file_answer_byte_identically(box):
    """(a) A 403 would tell a caller which files hold secrets: a target list. One 404 for both."""
    (box["repo"] / "conf.txt").write_text("x = " + SECRETS["github"] + "\n")
    secret, missing = _get(box, "conf.txt"), _get(box, "no-such-file.txt")
    drop = {"Date", "Server"}
    strip = lambda h: {k: v for k, v in h.items() if k not in drop}
    assert (secret[0], strip(secret[1]), secret[2]) == (missing[0], strip(missing[1]), missing[2])
    assert secret[0] == 404
    assert _audit(box)[-2]["reason"] == "secret-shaped:github-token"


def test_the_fleet_bearer_does_not_pass_code(box):
    """(b) `code` is device-token-only: the legacy fleet bearer is pending rotation and once sat on
    public surfaces, so '*' expands to every verb EXCEPT code."""
    st, _, _ = _get(box, "README.md", token=FLEET)
    assert st == 403
    from scripts.device_tokens import scopes_allow
    assert scopes_allow(("*",), "code") is False and scopes_allow(("*",), "inject") is True


@pytest.mark.parametrize("peer,ok", [("127.0.0.1", True), ("::1", True), ("100.64.0.7", True),
                                     ("100.127.255.254", True), ("fd7a:115c:a1e0::1a", True),
                                     ("100.128.0.1", False), ("203.0.113.5", False), ("10.0.0.2", False),
                                     ("2001:db8::1", False), ("", False), ("not-an-ip", False),
                                     ("::ffff:127.0.0.1", True), ("::ffff:203.0.113.5", False)])
def test_only_loopback_and_tailnet_peers_are_served(box, monkeypatch, peer, ok):
    """(c) A Funnel request that somehow lacks the header still fails on its peer address."""
    monkeypatch.setattr(G, "_agent_file_peer", lambda request: peer)
    st, _, _ = _get(box, "README.md")
    assert (st == 200) is ok, (peer, st)
