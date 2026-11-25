"""JevClient: provider selection, credentials, and the live transport against a loopback fake."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jev import client as jc
from jev.helpers import choice, noul, score

KEY = "apikey_TEST_never_print_me"
Q = {"n": noul("Is it?"), "c": choice("Which?", {"a": "A", "b": "B"}), "s": score("How?", ["lo", "mid", "hi"])}


def good_response(**over):
    body = {
        "model": jc.PINNED_MODEL,
        "answers": {
            "n": {"type": "noul", "noul": 0.9},
            "c": {"type": "choice", "choice": "a", "probabilities": {"a": 0.8, "b": 0.2}, "confidence": 0.8},
            "s": {"type": "score", "score": 1.2, "legend": {"0": "lo", "1": "mid", "2": "hi"},
                  "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3}, "confidence": 0.6},
        },
        "usage": {"input_tokens": 100, "output_tokens": 3},
        "provider_extension": {"kept": True},
    }
    body.update(over)
    return body


class FakeProvider:
    """Scripted responses: each item is (status, body_or_None, headers, delay_s)."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"auth": self.headers.get("Authorization"), "body": json.loads(self.rfile.read(n)),
                                       "path": self.path})
                status, body, headers, delay = outer.script.pop(0) if outer.script else (500, None, {}, 0)
                if delay:
                    time.sleep(delay)
                raw = body if isinstance(body, (bytes, str)) else json.dumps(body or {})
                raw = raw.encode() if isinstance(raw, str) else raw
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/systemone"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    servers = []

    def make(*script):
        s = FakeProvider(script)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def live(url, **kw):
    kw.setdefault("retry_delay_ms", 0)
    return jc.JevClient(provider="typesafe", api_key=KEY, base_url=url, **kw)


# ---------------------------------------------------------------- selection

def test_pytest_isolation_selects_mock_even_with_ambient_keys(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "apikey_ambient")
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    assert jc.select_provider() == "mock"
    assert jc.JevClient().provider == "mock"


def test_jev_live_lifts_isolation_and_backend_is_honoured(monkeypatch):
    monkeypatch.setenv("JEV_LIVE", "1")
    monkeypatch.setenv("JEV_BACKEND", "openrouter")
    assert jc.select_provider() == "openrouter"
    monkeypatch.setenv("JEV_BACKEND", "bogus")
    with pytest.raises(jc.JevConfigError, match="Unknown JEV backend"):
        jc.select_provider()


def test_auto_selection_prefers_typesafe_then_openrouter(monkeypatch):
    monkeypatch.setenv("JEV_LIVE", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert jc.select_provider() == "openrouter"
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    assert jc.select_provider() == "typesafe"


def test_no_credentials_fails_closed(monkeypatch):
    monkeypatch.setenv("JEV_LIVE", "1")
    with pytest.raises(jc.JevConfigError, match="No Jev credentials"):
        jc.select_provider()
    with pytest.raises(jc.JevConfigError, match="needs a nonblank api_key"):
        jc.JevClient(provider="typesafe")


def test_api_key_requires_explicit_live_provider():
    with pytest.raises(ValueError, match="explicit live provider"):
        jc.JevClient(api_key="x")
    with pytest.raises(ValueError, match="explicit live provider"):
        jc.JevClient(provider="mock", api_key="x")


def test_dotenv_key_is_read_when_not_isolated(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nexport TYPESAFE_API_KEY='apikey_from_dotenv'\n")  # pragma: allowlist secret (fake test fixture)
    monkeypatch.setenv("JEV_DOTENV", str(env))
    assert jc._read_dotenv_key("TYPESAFE_API_KEY", str(env)) == "apikey_from_dotenv"
    assert jc._read_dotenv_key("MISSING", str(env)) is None
    assert jc._env_or_dotenv("TYPESAFE_API_KEY") is None  # isolated: the file is never consulted
    monkeypatch.setenv("JEV_LIVE", "1")
    assert jc._env_or_dotenv("TYPESAFE_API_KEY") == "apikey_from_dotenv"
    assert jc.JevClient(provider="typesafe")._api_key == "apikey_from_dotenv"  # pragma: allowlist secret (fake test fixture)


def test_constructor_validates_options():
    with pytest.raises(ValueError, match="timeout_ms"):
        jc.JevClient(provider="mock", timeout_ms=0)
    with pytest.raises(ValueError, match="retry_delay_ms"):
        jc.JevClient(provider="mock", retry_delay_ms=-1)
    with pytest.raises(ValueError, match="pricing"):
        jc.JevClient(provider="mock", pricing={"input_per_million": -1, "output_per_million": 0})


def test_mock_client_result_meta_and_events():
    c = jc.JevClient(provider="mock")
    events = []
    off = c.on(events.append)
    r = c.system_one("hello there", {"q": noul("Is this a greeting?")})
    assert r["answers"]["q"]["type"] == "noul"
    assert r["meta"]["provider"] == "mock" and r["meta"]["cost"] == {"amount": 0, "source": "mock"}
    assert r["raw"]["request"]["model"] == "jev-1.13.0-mock"
    assert [e["kind"] for e in events] == ["request", "response"] and c.calls == 1
    off()
    c.system_one("x", {"q": noul("y")})
    assert len(events) == 2


def test_invalid_questions_never_reach_transport(fake):
    srv = fake()
    with pytest.raises(jc.QuestionValidationError):
        live(srv.url).system_one("s", {"q": {"type": "noul", "instructions": ""}})
    assert srv.requests == []


# ---------------------------------------------------------------- live transport (loopback)

def test_live_success_sends_bearer_and_keeps_extensions(fake):
    srv = fake((200, good_response(), {}, 0))
    c = live(srv.url)
    r = c.system_one({"x": 1}, Q)
    assert srv.requests[0]["auth"] == f"Bearer {KEY}"
    assert srv.requests[0]["body"]["model"] == "jev-1.14.0", "the pinned id, never the moving alias"
    assert r["answers"]["c"]["choice"] == "a" and r["provider_extension"] == {"kept": True}
    assert r["meta"]["attempts"] == 1 and r["meta"]["resolved_model"] == "jev-1.14.0"
    assert r["meta"]["cost"] == {"amount": None, "source": "unknown"}
    assert KEY not in json.dumps(r) and KEY not in repr(c)


def test_openrouter_default_model(fake):
    srv = fake((200, good_response(), {}, 0))
    jc.JevClient(provider="openrouter", api_key=KEY, base_url=srv.url).system_one("s", Q)
    assert srv.requests[0]["body"]["model"] == "~typesafe/jev-latest"


def test_cost_reported_beats_estimate(fake):
    srv = fake((200, good_response(usage={"input_tokens": 1_000_000, "output_tokens": 0, "cost": 0.05}), {}, 0),
               (200, good_response(usage={"input_tokens": 1_000_000, "output_tokens": 0}), {}, 0))
    c = live(srv.url, pricing={"input_per_million": 0.042, "output_per_million": 0})
    assert c.system_one("s", Q)["meta"]["cost"] == {"amount": 0.05, "source": "reported"}
    assert c.system_one("s", Q)["meta"]["cost"] == {"amount": 0.042, "source": "estimated"}


def test_retries_on_429_then_succeeds(fake):
    srv = fake((429, None, {"Retry-After": "0"}, 0), (200, good_response(), {}, 0))
    r = live(srv.url).system_one("s", Q)
    assert r["meta"]["attempts"] == 2 and len(srv.requests) == 2


def test_gives_up_after_three_attempts(fake):
    srv = fake((503, None, {}, 0), (503, None, {}, 0), (503, None, {}, 0), (200, good_response(), {}, 0))
    with pytest.raises(jc.JevHTTPError, match="HTTP 503"):
        live(srv.url).system_one("s", Q)
    assert len(srv.requests) == 3


@pytest.mark.parametrize("status, hint", [(401, "TYPESAFE_API_KEY"), (402, "credits"), (400, "")])
def test_non_retry_statuses_fail_once_with_hint(fake, status, hint):
    srv = fake((status, None, {}, 0))
    with pytest.raises(jc.JevHTTPError, match=f"HTTP {status}") as ei:
        live(srv.url).system_one("s", Q)
    assert hint in str(ei.value) and KEY not in str(ei.value)
    assert len(srv.requests) == 1


def test_redirects_are_refused(fake):
    srv = fake((302, None, {"Location": "http://127.0.0.1:1/elsewhere"}, 0))
    with pytest.raises(jc.JevHTTPError, match="Redirects are refused"):
        live(srv.url).system_one("s", Q)


def test_timeout_budget_covers_the_whole_call(fake):
    srv = fake((200, good_response(), {}, 1.5))
    t0 = time.monotonic()
    with pytest.raises(jc.JevTimeout):
        live(srv.url, timeout_ms=300).system_one("s", Q)
    assert time.monotonic() - t0 < 1.4


def test_unreachable_endpoint_is_a_transport_error():
    with pytest.raises(jc.JevTransportError):
        live("http://127.0.0.1:9/v1/systemone").system_one("s", Q)


def test_outbound_fence_blocks_real_hosts():
    with pytest.raises(jc.JevError):
        jc.JevClient(provider="typesafe", api_key=KEY, retry_delay_ms=0).system_one("s", Q)


@pytest.mark.parametrize("mutate, msg", [
    (lambda b: "not json", "Invalid response JSON"),
    (lambda b: {**b, "model": ""}, "envelope"),
    (lambda b: {**b, "usage": {"input_tokens": -1, "output_tokens": 0}}, "usage"),
    (lambda b: {**b, "answers": {k: v for k, v in b["answers"].items() if k != "n"}}, "Missing or mismatched answer: n"),
    (lambda b: {**b, "answers": {**b["answers"], "n": {"type": "noul", "noul": 1.2}}}, "Invalid noul"),
    (lambda b: {**b, "answers": {**b["answers"], "c": {**b["answers"]["c"], "choice": "zzz"}}}, "Undeclared choice"),
    (lambda b: {**b, "answers": {**b["answers"], "c": {**b["answers"]["c"], "probabilities": {"a": 0.5, "b": 0.1}}}}, "sum to one"),
    (lambda b: {**b, "answers": {**b["answers"], "c": {**b["answers"]["c"], "probabilities": {"a": 1.0}}}}, "keys must match"),
    (lambda b: {**b, "answers": {**b["answers"], "s": {**b["answers"]["s"], "score": 2.5}}}, "Score out of range"),
    (lambda b: {**b, "answers": {**b["answers"], "s": {**b["answers"]["s"], "legend": {"0": "lo", "1": "MID", "2": "hi"}}}}, "legend"),
    (lambda b: {**b, "answers": {**b["answers"], "c": {**b["answers"]["c"], "confidence": True}}}, "distribution"),
])
def test_contract_violations_raise(fake, mutate, msg):
    srv = fake((200, mutate(good_response()), {}, 0))
    with pytest.raises(jc.ContractError, match=msg):
        live(srv.url).system_one("s", Q)


def test_retry_after_parsing():
    assert jc.retry_after_ms(None) == 0
    assert jc.retry_after_ms("2") == 2000
    assert jc.retry_after_ms("1.5") == 1500
    assert jc.retry_after_ms("-1") == 0
    assert jc.retry_after_ms("soon") == 0
    future = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 30))
    assert 25_000 < jc.retry_after_ms(future) <= 31_000


# ---------------------------------------------------------------- model pin

def test_a_different_served_model_is_refused_for_an_exact_request(fake):
    srv = fake((200, good_response(model="jev-1.15.0"), {}, 0))
    with pytest.raises(jc.ModelMismatchError) as e:
        live(srv.url).system_one("s", Q)
    assert isinstance(e.value, jc.ContractError) and isinstance(e.value, jc.JevError), "hooks treat it as a failed call"
    assert len(srv.requests) == 1, "a mismatch is not retried"


def test_an_exact_request_answered_by_that_model_is_accepted(fake):
    srv = fake((200, good_response(model="jev-1.13.0"), {}, 0))
    r = live(srv.url).system_one("s", Q, model="jev-1.13.0")
    assert r["meta"]["requested_model"] == r["meta"]["resolved_model"] == "jev-1.13.0"


def test_an_alias_request_accepts_whatever_model_serves_it(fake):
    srv = fake((200, good_response(model="jev-1.15.0"), {}, 0), (200, good_response(model="x-9"), {}, 0))
    assert live(srv.url).system_one("s", Q, model="jev-latest")["meta"]["resolved_model"] == "jev-1.15.0"
    jc.JevClient(provider="openrouter", api_key=KEY, base_url=srv.url).system_one("s", Q)


def test_alias_detection():
    assert jc.is_alias("jev-latest") and jc.is_alias("~typesafe/jev-latest")
    assert not jc.is_alias(jc.PINNED_MODEL) and not jc.is_alias("jev-1.13.0")
