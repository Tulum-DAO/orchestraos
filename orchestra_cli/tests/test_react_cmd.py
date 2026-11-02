"""`orchestra react` (spec "Agent path"): an agent reacts to a message in its own chat. A fake API on an ephemeral
port behaves like api/src/routes/reactions.ts (PUT toggles; an agent only with the right token, else the operator)
and serves a chat transcript whose items carry server refs."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import react_cmd as R

TOKEN = "tok-1"
OPERATOR = {"kind": "user", "id": "op"}
CHAT = [
    {"kind": "text", "role": "user", "text": "first ask", "ref": "s1~u1#0"},
    {"kind": "text", "role": "assistant", "text": "done", "ref": "s1~a1#0"},
    {"kind": "text", "role": "user", "text": "ship it", "ref": "s1~u2#0"},           # the newest the operator said
    {"kind": "text", "role": "user", "text": "[from gm] status?", "ref": "s1~m1#0", "machine": "relay"},
    {"kind": "text", "role": "user", "text": "", "ref": "s1~w1#0", "reaction_wake": True},
    {"kind": "text", "role": "user", "text": "boot brief", "ref": "s1~b1#0", "is_system": True},
    {"kind": "text", "role": "user", "text": "said on the call", "ref": "s1~v1#0", "voice_said": True},
    {"kind": "text", "role": "user", "text": "pending", "uuid": "queued:9", "queued": True},     # no ref
    {"kind": "text", "role": "assistant", "text": "on it", "ref": "s1~a2#0"},
]


class FakeApi:
    def __init__(self):
        self.items = list(CHAT)
        self.rows = {}          # ref -> [{emoji, reactor, at}]
        self.calls = []         # (method, path, lower-cased headers, body)
        self.accept_token = True

    def reactor(self, headers):
        agent, tok = headers.get("x-orchestra-agent"), headers.get("x-orchestra-agent-token")
        if agent and tok == TOKEN and self.accept_token:
            return {"kind": "agent", "id": agent}
        return dict(OPERATOR)


def _handler(api):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, doc):
            raw = json.dumps(doc).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _do(self, method):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            api.calls.append((method, u.path, hdrs, body))
            if method == "GET" and u.path.endswith("/transcript"):
                return self._send(200, {"items": api.items})
            if u.path != "/api/reactions":
                return self._send(404, {"error": "not found"})
            if method == "GET":
                return self._send(200, {"surface": q.get("surface"), "conv": q.get("conv"), "reactions": api.rows})
            if hdrs.get("x-orchestra-agent") and not (hdrs.get("x-orchestra-agent-token") == TOKEN and api.accept_token):
                return self._send(401, {"error": "agent_token_invalid"})   # commit b192913: nothing stored
            who = api.reactor(hdrs)
            before = api.rows.get(body["ref"], [])
            mine = next((r for r in before if r["reactor"] == who), None)
            rows = [r for r in before if r["reactor"] != who]
            if method == "DELETE":
                api.rows[body["ref"]] = rows
                return self._send(200, {"ok": True, "removed": mine is not None, "reactor": who, "emoji": None})
            emoji = None if mine and mine["emoji"] == body["emoji"] else body["emoji"]
            if emoji:
                rows.append({"emoji": emoji, "reactor": who, "at": "t"})
            api.rows[body["ref"]] = rows
            return self._send(200, {"ok": True, "surface": body["surface"], "conv": body["conv"], "ref": body["ref"],
                                    "reactor": who, "emoji": emoji, "at": "t"})

        def do_GET(self):
            self._do("GET")

        def do_PUT(self):
            self._do("PUT")

        def do_DELETE(self):
            self._do("DELETE")
    return H


@pytest.fixture()
def api(tmp_path, monkeypatch):
    fake = FakeApi()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = tmp_path / "agent-react-token"
    tok.write_text(TOKEN + "\n")
    monkeypatch.setenv("ORCH_API_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("ORCHESTRA_AGENT_REACT_TOKEN_FILE", str(tok))
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "pm-x")
    yield fake
    srv.shutdown()
    srv.server_close()


def writes(api):
    return [c for c in api.calls if c[0] in ("PUT", "DELETE")]


def test_parse():
    ns = M.parse_args(["react", "👍"])
    assert (ns.command, ns.emoji, ns.to, ns.last, ns.remove) == ("react", "👍", None, False, False)
    ns = M.parse_args(["react", "🎉", "--to", "s1~u1#0"])
    assert ns.to == "s1~u1#0"
    assert M.parse_args(["react", "--remove"]).remove is True
    with pytest.raises(SystemExit):
        M.parse_args(["react", "👍", "--to", "x", "--last"])


def test_default_is_the_operators_newest_message_with_both_headers(api, capsys):
    assert M.main(["react", "👍"]) == 0
    (method, path, hdrs, body), = writes(api)
    assert (method, path) == ("PUT", "/api/reactions")
    assert body == {"surface": "agent-chat", "conv": "pm-x", "ref": "s1~u2#0", "emoji": "👍"}
    assert hdrs["x-orchestra-agent"] == "pm-x" and hdrs["x-orchestra-agent-token"] == TOKEN
    assert api.rows["s1~u2#0"] == [{"emoji": "👍", "reactor": {"kind": "agent", "id": "pm-x"}, "at": "t"}]
    out = capsys.readouterr().out
    assert "reacted 👍 to s1~u2#0" in out and TOKEN not in out


def test_last_flag_is_the_default_spelled_out(api):
    assert M.main(["react", "👍", "--last"]) == 0
    assert writes(api)[0][3]["ref"] == "s1~u2#0"


def test_to_names_the_message(api):
    assert M.main(["react", "🎉", "--to", "s1~a1#0"]) == 0
    assert writes(api)[0][3]["ref"] == "s1~a1#0"
    assert not any(c[1].endswith("/transcript") for c in api.calls)       # no chat read needed


def test_the_same_emoji_again_changes_nothing(api, capsys):
    assert M.main(["react", "👍"]) == 0
    assert M.main(["react", "👍"]) == 0
    assert len(writes(api)) == 1                                           # the API would have toggled it off
    assert "already reacted 👍" in capsys.readouterr().out
    assert api.rows["s1~u2#0"][0]["emoji"] == "👍"


def test_another_emoji_replaces_it(api):
    assert M.main(["react", "👍"]) == 0
    assert M.main(["react", "❤️"]) == 0
    assert [r["emoji"] for r in api.rows["s1~u2#0"]] == ["❤️"]


def test_remove(api, capsys):
    assert M.main(["react", "--remove"]) == 0
    assert writes(api) == []                                               # nothing of ours there: no request
    assert "no reaction of yours" in capsys.readouterr().out
    assert M.main(["react", "👍"]) == 0
    assert M.main(["react", "--remove"]) == 0
    method, _path, hdrs, body = writes(api)[-1]
    assert method == "DELETE" and body == {"surface": "agent-chat", "conv": "pm-x", "ref": "s1~u2#0"}
    assert hdrs["x-orchestra-agent-token"] == TOKEN
    assert api.rows["s1~u2#0"] == []


def test_no_emoji_without_remove_is_a_usage_error(api, capsys):
    assert M.main(["react"]) == 2
    assert "which emoji" in capsys.readouterr().err
    assert api.calls == []


def test_who_am_i_falls_back_to_the_tmux_session(api, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tmux").write_text('#!/bin/sh\n[ "$1" = display-message ] && echo seat-from-tmux\nexit 0\n')
    (bin_dir / "tmux").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.delenv("ORCHESTRA_AGENT_ID")
    monkeypatch.setenv("TMUX", "/tmp/fake,1,0")
    assert M.main(["react", "👍", "--to", "s1~u2#0"]) == 0
    assert writes(api)[0][2]["x-orchestra-agent"] == "seat-from-tmux"


def test_no_identity_at_all(api, monkeypatch, capsys):
    monkeypatch.delenv("ORCHESTRA_AGENT_ID")
    monkeypatch.delenv("TMUX", raising=False)
    assert M.main(["react", "👍"]) == 2
    assert "ORCHESTRA_AGENT_ID" in capsys.readouterr().err
    assert api.calls == []


def test_no_token_file(api, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRA_AGENT_REACT_TOKEN_FILE", str(tmp_path / "missing"))
    assert M.main(["react", "👍"]) == 2
    assert "no agent react token" in capsys.readouterr().err
    assert api.calls == []


def test_a_token_the_api_does_not_accept_is_reported_and_nothing_is_undone(api, capsys):
    api.accept_token = False
    assert M.main(["react", "👍", "--to", "s1~a1#0"]) == 1
    assert [c[0] for c in writes(api)] == ["PUT"]                         # the API stored nothing: no undo-delete
    assert api.rows == {}
    err = capsys.readouterr().err
    assert "agent_token_invalid" in err and "agent token" in err


def test_remove_with_a_refused_token_is_reported_too(api, capsys):
    assert M.main(["react", "👍"]) == 0
    api.accept_token = False
    assert M.main(["react", "--remove"]) == 1
    assert [c[0] for c in writes(api)] == ["PUT", "DELETE"]
    assert "agent_token_invalid" in capsys.readouterr().err


def test_nothing_from_the_operator_to_react_to(api, capsys):
    api.items = [i for i in CHAT if i["role"] == "assistant" or i.get("machine")]
    assert M.main(["react", "👍"]) == 2
    assert "no message from the operator" in capsys.readouterr().err
    assert writes(api) == []


def test_api_base_from_orchestra_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCH_API_URL", raising=False)
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[api]\nhost = "0.0.0.0"\nport = 18999\n')
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    assert R.api_base() == "http://127.0.0.1:18999"


def test_token_file_defaults_to_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRA_AGENT_REACT_TOKEN_FILE", raising=False)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert R.token_file() == tmp_path / "state" / "agent-react-token"


def test_unreachable_api(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORCH_API_URL", "http://127.0.0.1:9")                  # discard port: nothing listens
    tok = tmp_path / "t"
    tok.write_text(TOKEN)
    monkeypatch.setenv("ORCHESTRA_AGENT_REACT_TOKEN_FILE", str(tok))
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "pm-x")
    assert M.main(["react", "👍", "--to", "s1~u2#0"]) == 1
    assert "could not reach the API" in capsys.readouterr().err
