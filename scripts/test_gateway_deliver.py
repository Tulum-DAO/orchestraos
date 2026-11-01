"""Words for a headless seat go through the gateway's /agent-message, never into its pane (its pane holds the seat's
supervisor). The shared client (gateway_deliver), each rerouted injector, and the overhead of the gateway hop
against handing the words to the supervisor directly. Stand-ins only: a local HTTP stand-in or the real gateway
handler on a loopback port this test opens, a fake supervisor on a temp unix socket, temp data dirs, no tmux."""
import json
import os
import shutil
import socket
import statistics
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gateway_deliver as GD  # noqa: E402
from scripts import gateway_deliver as GDS  # noqa: E402  (the lineage daemon imports it as a package module)


# ------------------------------------------------------------------ a stand-in gateway
class _StandIn:
    """Answers POST /agent-message with a scripted (status, body); records what it got."""

    def __init__(self, status=200, body=None):
        self.status, self.body, self.got = status, body or {"ok": True, "delivered": True}, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.got.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                  "actor": self.headers.get("X-Actor"), "json": json.loads(self.rfile.read(n))})
                data = json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def standin(tmp_path, monkeypatch):
    tok = tmp_path / "token"
    tok.write_text("test-token\n")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tok))
    s = _StandIn()
    yield s
    s.close()


def test_deliver_posts_the_words_with_the_gateways_bearer(standin):
    r = GD.deliver("dev-demo", "hello", url=standin.url, actor="automation:test")
    assert r == {"ok": True, "delivered": True, "held": False, "status": 200, "reason": "",
                 "body": {"ok": True, "delivered": True}}
    got = standin.got[0]
    assert got["path"] == "/agent-message" and got["auth"] == "Bearer test-token"
    assert got["actor"] == "automation:test"
    assert got["json"] == {"session": "dev-demo", "text": "hello", "source": "mail"}


def test_deliver_passes_force_held_and_person(standin):
    GD.deliver("dev-demo", "x", url=standin.url, force=True, accepts_held=True, source="person")
    assert standin.got[0]["json"] == {"session": "dev-demo", "text": "x", "source": "person", "force": True,
                                      "accepts": ["held"]}


@pytest.mark.parametrize("status,body,want", [
    (200, {"ok": True, "delivered": False, "held": True}, {"ok": True, "delivered": False, "held": True}),
    (409, {"ok": False, "busy": True, "reason": "busy"}, {"ok": False, "delivered": False, "reason": "busy"}),
    (502, {"ok": False, "delivered": False, "reason": "unverified_submit"},
     {"ok": False, "reason": "unverified_submit"}),
    (404, {"ok": False, "error": "no such session"}, {"ok": False, "reason": "no such session"}),
])
def test_deliver_reads_every_answer_shape(standin, status, body, want):
    standin.status, standin.body = status, body
    r = GD.deliver("dev-demo", "x", url=standin.url)
    assert r["status"] == status and {k: r[k] for k in want} == want


def test_deliver_unreachable_is_status_0_never_a_raise():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]          # closed again: nothing listens there
    r = GD.deliver("dev-demo", "x", url=f"http://127.0.0.1:{port}", timeout=2)
    assert r["status"] == 0 and not r["ok"] and r["reason"].startswith("gateway_unreachable:")


def test_deliver_never_reaches_the_live_gateway_from_a_test():
    r = GD.deliver("dev-demo", "x")
    assert r["status"] == 599 and not r["ok"]


# ------------------------------------------------------------------ headless switch in a temp data dir
@pytest.fixture
def orch(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="gd-", dir="/tmp"))      # short: unix socket paths are length-capped
    d = root / "d"
    (d / "state").mkdir(parents=True)
    (d / "registry.json").write_text(json.dumps({"agents": {
        "dev-demo": {"runtime": "claude", "tmux_session": "dev-demo"},
        "pm-demo": {"runtime": "claude", "tmux_session": "pm-demo"}}}))
    (d / "state" / "headless-seats.json").write_text(json.dumps({"seats": {
        "dev-demo": {"headless": True, "runtime": "claude"}}}))
    monkeypatch.setenv("ORCHESTRA_DIR", str(d))
    monkeypatch.setenv("ORCH_DIR", str(d))
    yield d
    shutil.rmtree(root, ignore_errors=True)


def test_headless_seat_reads_the_switch(orch):
    import headless_switch as HS
    assert HS.switch_path().name == "headless-seats.json"
    assert GD.headless_seat("dev-demo") == "dev-demo" and GD.headless_seat("pm-demo") is None
    (orch / "state" / "headless" / "dev-demo").mkdir(parents=True)
    (orch / "state" / "headless" / "dev-demo" / "dev").write_text("")
    assert GD.headless_seat("dev-demo") is None          # in Dev the pane IS its TUI: the pane paths apply


# ------------------------------------------------------------------ nudge_pane
def test_nudge_to_a_headless_seat_goes_through_the_gateway_and_types_nothing(orch, monkeypatch):
    import nudge_pane as NP
    sent = []
    monkeypatch.setattr(NP, "tmux", lambda *a: pytest.fail(f"typed into a headless pane: {a}"))
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: sent.append((a, k)) or
                        {"ok": True, "delivered": True, "held": False, "status": 200, "reason": "", "body": {}})
    assert NP.nudge("dev-demo", "continue please") == 0
    assert sent[0][0] == ("dev-demo", "continue please") and sent[0][1]["source"] == "mail"


@pytest.mark.parametrize("status,rc", [(409, 2), (502, 2), (0, 3)])
def test_nudge_headless_refusals_keep_the_exit_codes(status, rc):
    import nudge_pane as NP
    r = {"ok": False, "delivered": False, "held": False, "status": status, "reason": "busy", "body": {}}
    assert NP.nudge_headless("dev-demo", "x", deliver=lambda *a, **k: r) == rc


def test_nudge_to_a_tmux_seat_keeps_its_own_typer(orch, monkeypatch):
    import nudge_pane as NP
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: pytest.fail("a tmux seat's nudge went to the gateway"))
    monkeypatch.setenv("PANE_LOCK_DIR", str(orch / "locks"))
    monkeypatch.setattr(NP, "_nudge_unlocked", lambda s, t, **k: 0)
    assert NP.nudge("pm-demo", "hello") == 0


# ------------------------------------------------------------------ boot_inject
def _ok(**kw):
    return {"ok": True, "delivered": True, "held": False, "status": 200, "reason": "", "body": {}, **kw}


def test_boot_prompt_to_a_headless_seat_goes_through_the_gateway(orch, monkeypatch):
    import boot_inject as BI
    sent = []
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: sent.append(a) or _ok())
    out = BI.boot_inject("dev-demo", "You are dev-demo.", tmux_fn=lambda *a, **k: pytest.fail("pane touched"),
                         canonical_live_fn=lambda s, **k: False, log_fn=lambda m: None)
    assert out == ("submitted", "headless-gateway") and sent == [("dev-demo", "You are dev-demo.")]


def test_boot_prompt_to_a_live_headless_seat_needs_allow_canonical(orch, monkeypatch):
    import boot_inject as BI
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: pytest.fail("sent to a live seat without allow_canonical"))
    out = BI.boot_inject("dev-demo", "x", canonical_live_fn=lambda s, **k: True, log_fn=lambda m: None)
    assert out == ("failed", "refused-canonical")
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: _ok())
    assert BI.boot_inject("dev-demo", "x", allow_canonical=True, canonical_live_fn=lambda s, **k: True,
                          log_fn=lambda m: None) == ("submitted", "headless-gateway")


@pytest.mark.parametrize("status,want", [(409, ("stuck", "headless-busy")), (502, ("failed", "headless-refused")),
                                         (0, ("failed", "headless-unreachable"))])
def test_boot_prompt_headless_outcomes(status, want):
    import boot_inject as BI
    r = {"ok": False, "delivered": False, "held": False, "status": status, "reason": "x", "body": {}}
    assert BI.boot_inject_headless("dev-demo", "x", canonical_live_fn=lambda s, **k: False,
                                   deliver=lambda *a, **k: r, log_fn=lambda m: None) == want


def test_boot_prompt_to_a_tmux_seat_keeps_the_pane_path(orch, monkeypatch):
    import boot_inject as BI
    monkeypatch.setattr(GD, "deliver", lambda *a, **k: pytest.fail("a tmux boot went to the gateway"))
    monkeypatch.setenv("PANE_LOCK_DIR", str(orch / "locks"))
    monkeypatch.setattr(BI, "_boot_inject_unlocked", lambda *a, **k: ("submitted", "enter-1"))
    assert BI.boot_inject("pm-demo", "x") == ("submitted", "enter-1")


# ------------------------------------------------------------------ bus_beat's wake (gm)
@pytest.fixture
def bus_beat(monkeypatch):
    from scripts.lineage_daemon import bus_beat as BB
    return BB


def test_armed_wake_carries_the_bearer(bus_beat, monkeypatch, standin):
    real = GDS.deliver
    monkeypatch.setattr(GDS, "deliver", lambda *a, **k: real(*a, url=standin.url, **k))
    ok, info = bus_beat._wake_inject("digest", True)
    assert ok and standin.got[0]["auth"] == "Bearer test-token"
    assert standin.got[0]["json"]["session"] == "gm"


def test_dry_wake_types_nothing_into_a_headless_gm(bus_beat, monkeypatch):
    monkeypatch.setattr(bus_beat, "_gm_headless", lambda: "gm")
    monkeypatch.setattr(bus_beat, "_wake_inject", bus_beat._wake_inject)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"pane touched: {a}"))
    assert bus_beat._wake_inject("digest", False) == (True, {"reason": "dry_headless", "would_submit": True})


def test_a_headless_gm_reads_as_an_empty_composer(bus_beat, monkeypatch):
    from scripts.focus_registry import wake
    monkeypatch.setattr(bus_beat, "_gm_headless", lambda: "gm")
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("captured a headless pane"))
    comp = bus_beat._gm_composer()
    assert wake._composer_verdict(comp) == "ok"


# ------------------------------------------------------------------ lineage wakes after a durable row
def test_s3_correction_nudge_skips_a_headless_successor(monkeypatch):
    from scripts.lineage_daemon import s3_live_seams as S3
    import subprocess
    monkeypatch.setattr(GDS, "headless_seat", lambda s, orch_dir=None: s)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"pane touched: {a}"))
    sent = []
    S3._inject_correction("dev-demo", "text", "n1", send_fn=lambda *a: sent.append(a))
    assert sent == [("dev-demo", "text", "n1")]          # the durable row still goes


def test_s3_correction_nudge_still_wakes_a_tmux_successor(monkeypatch):
    from scripts.lineage_daemon import s3_live_seams as S3
    import subprocess
    calls = []
    monkeypatch.setattr(GDS, "headless_seat", lambda s, orch_dir=None: None)
    monkeypatch.setattr(subprocess, "run", lambda a, **k: calls.append(a) or types.SimpleNamespace(stdout="", returncode=0))
    S3._inject_correction("pm-demo", "text", "n1", send_fn=lambda *a: None)
    assert ["tmux", "send-keys", "-t", "pm-demo:0.0", "", "Enter"] in calls


def test_lineage_resume_echo_skips_a_headless_canary():
    from scripts.lineage_daemon import complete as C
    calls = []
    assert C.resume_pane_echo("dev-demo", "t1", run=lambda a, **k: calls.append(a), headless_fn=lambda s: s) is False
    assert calls == []
    assert C.resume_pane_echo("pm-demo", "t1", run=lambda a, **k: calls.append(a), headless_fn=lambda s: None)
    assert calls == [["tmux", "send-keys", "-t", "=pm-demo:0", "echo '[LINEAGE RESUME] Resuming task: t1'\n"]]


# ------------------------------------------------------------------ red_alert_watch key repairs
def test_red_alert_key_repairs_never_type_into_a_headless_pane(monkeypatch, tmp_path):
    import red_alert as RA
    import red_alert_watch as RAW
    monkeypatch.setenv("PANE_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(RAW, "decide", lambda *a, **k: {"action": "repair", "fix": "bare_enter", "reason": "t"})
    monkeypatch.setattr(RAW, "_view", lambda r: r)
    monkeypatch.setattr(RAW, "norm_answer", lambda c: None)
    monkeypatch.setattr(RAW, "armed", lambda: True)
    monkeypatch.setitem(RAW.REPAIRS, "bare_enter", lambda *a: pytest.fail("keyed a headless pane"))
    attempts = []
    monkeypatch.setattr(RA, "record_attempt", lambda rid, **k: attempts.append(k))
    monkeypatch.setattr(RA, "update", lambda rid, **k: None)
    monkeypatch.setattr(RAW, "_headless", lambda s: True)
    RAW.handle("dev-demo", "dev-demo", {"id": "ra_x", "class": "pane_dead", "card_id": None, "status": "open"},
               {"attached": {}}, dry=False)
    assert attempts == []                               # not a failed repair: no escalation count


def test_red_alert_respawn_is_not_a_key_repair():
    import red_alert_watch as RAW
    assert "respawn_resume" not in RAW.KEY_REPAIRS
    assert set(RAW.KEY_REPAIRS) | {"respawn_resume"} == set(RAW.REPAIRS)


# ------------------------------------------------------------------ the gateway hop's overhead
class FakeSupervisor:
    """The supervisor's socket protocol: `send` answers queued at once, like a warm supervisor."""

    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(64)
        self.sent = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with c:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf or b"{}")
                if req.get("cmd") == "send":
                    self.sent.append(req.get("text"))
                c.sendall((json.dumps({"ok": True, "state": "queued"}) + "\n").encode())

    def close(self):
        self.sock.close()


def _real_gateway(monkeypatch, orch):
    """The real POST /agent-message handler (auth, refusal checks, verified_inject, the headless path) served on a
    loopback port this test opens. Only tmux's session list is stood in (no tmux in a test)."""
    import asyncio
    from aiohttp import web
    # Importing the gateway loads Arturo's proxy, which prefetches its speech model into the data dir: never from a
    # test (a temp data dir has none, so it would download one).
    monkeypatch.setenv("ARTURO_LOCAL_STT", "0")
    if "services.arturo.local_stt" in sys.modules:
        monkeypatch.setattr(sys.modules["services.arturo.local_stt"], "ENABLED", False)
    import watch_gateway as G
    monkeypatch.setattr(G, "ORCH_DIR", orch)
    monkeypatch.setattr(G, "PROTECTED_SESSIONS_PATH", orch / "state" / "protected-sessions.json")
    monkeypatch.setattr(G, "gateway_token", lambda: "test-token")
    asked = []
    monkeypatch.setattr(G, "_tmux_session_names", lambda: asked.append(1) or {"pm-demo"})
    loop = asyncio.new_event_loop()
    started = threading.Event()
    box = {}

    async def _start():
        app = web.Application()
        app.router.add_post("/agent-message", G.handle_agent_message)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        box["runner"], box["port"] = runner, site._server.sockets[0].getsockname()[1]
        started.set()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_start())
        loop.run_forever()
    threading.Thread(target=_run, daemon=True).start()
    assert started.wait(10)

    def stop():
        asyncio.run_coroutine_threadsafe(box["runner"].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
    stop.asked = asked
    return f"http://127.0.0.1:{box['port']}", stop


def test_the_gateway_hop_adds_no_measurable_delay(orch, monkeypatch, tmp_path):
    """Direct (the router's path: the supervisor's socket) against rerouted (POST /agent-message through the real
    handler to the same supervisor). Each path 60 sends after 5 warm-ups; medians and p95 printed."""
    import headless_switch as HS
    tok = tmp_path / "token"
    tok.write_text("test-token")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tok))
    sup = FakeSupervisor(HS.socket_path("dev-demo"))
    url, stop = _real_gateway(monkeypatch, orch)
    try:
        def direct(i):
            return HS.send("dev-demo", f"direct {i}", f"m-d{i}")["ok"]

        def rerouted(i):
            return GD.deliver("dev-demo", f"rerouted {i}", url=url, actor="automation:test")["delivered"]
        res = {}
        for name, fn in (("direct", direct), ("rerouted", rerouted)):
            for i in range(5):
                assert fn(i)
            ts = []
            for i in range(60):
                t0 = time.perf_counter()
                assert fn(i)
                ts.append((time.perf_counter() - t0) * 1000)
            ts.sort()
            res[name] = (statistics.median(ts), ts[int(len(ts) * 0.95) - 1])
        over = res["rerouted"][0] - res["direct"][0]
        print(f"\nDELIVERY direct median {res['direct'][0]:.2f} ms p95 {res['direct'][1]:.2f} ms | "
              f"rerouted median {res['rerouted'][0]:.2f} ms p95 {res['rerouted'][1]:.2f} ms | "
              f"overhead median {over:.2f} ms")
        assert sup.sent.count("rerouted 0") == 2          # warm-up + measured: the words reached the supervisor
        assert stop.asked == []                           # a headless seat never waits on tmux's session list
        assert over < 20.0                                # against a ~1.5-2.9 s model turn: not measurable
    finally:
        stop()
        sup.close()


def test_a_pane_seat_still_needs_its_tmux_session(orch, monkeypatch, tmp_path):
    tok = tmp_path / "token"
    tok.write_text("test-token")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tok))
    url, stop = _real_gateway(monkeypatch, orch)
    try:
        r = GD.deliver("qa-demo", "x", url=url)               # not headless, not a tmux session
        assert r["status"] == 404 and r["reason"] == "no such session" and stop.asked == [1]
    finally:
        stop()


# ------------------------------------------------------------------ where the gateway is, outside `orchestra up`
@pytest.fixture
def plain_shell(tmp_path, monkeypatch):
    """No WATCH_GATEWAY_* in the env (a plain shell, cron): an install's own config and data dir in a temp dir."""
    for k in ("WATCH_GATEWAY_URL", "WATCH_GATEWAY_TOKEN_FILE"):
        monkeypatch.delenv(k, raising=False)
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[data]\ndir = "%s"\n\n[gateway]\nhost = "127.0.0.1"\nport = 8890\n' % data)
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return types.SimpleNamespace(data=data, cfg=cfg, home=tmp_path / "home")


def test_url_and_token_come_from_the_install_settings_without_the_env(plain_shell):
    tok = plain_shell.data / "state" / "watch-gateway-token"
    tok.write_text("from-settings\n")
    assert GD.gateway_url() == "http://127.0.0.1:8890"
    assert GD.token_path() == tok and GD.gateway_token() == "from-settings"


def test_the_env_still_wins(plain_shell, monkeypatch, tmp_path):
    other = tmp_path / "other-token"
    other.write_text("from-env")
    monkeypatch.setenv("WATCH_GATEWAY_URL", "http://127.0.0.1:7001/")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(other))
    assert GD.gateway_url() == "http://127.0.0.1:7001" and GD.gateway_token() == "from-env"


def test_no_data_dir_token_falls_back_to_the_legacy_file(plain_shell):
    legacy = plain_shell.home / ".config" / "jarvis" / "watch-gateway-token"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy")
    assert GD.token_path() == legacy and GD.gateway_token() == "legacy"


def test_unreadable_settings_fall_back_to_the_last_default(plain_shell, monkeypatch):
    monkeypatch.setattr(GD, "_settings", lambda: None)
    assert GD.gateway_url() == GD.DEFAULT_URL


def test_a_plain_shell_send_reaches_the_configured_gateway(plain_shell, monkeypatch):
    s = _StandIn()
    try:
        port = s.srv.server_address[1]
        plain_shell.cfg.write_text('[data]\ndir = "%s"\n\n[gateway]\nhost = "127.0.0.1"\nport = %d\n'
                                   % (plain_shell.data, port))
        (plain_shell.data / "state" / "watch-gateway-token").write_text("cfg-token")
        r = GD.deliver("dev-demo", "x", urlopen=__import__("urllib.request").request.urlopen)
        assert r["delivered"] and s.got[0]["auth"] == "Bearer cfg-token"
    finally:
        s.close()
