"""One https address for `orchestra pair` and Arturo's pair_device (scripts/public_url.py).

pm-tulumdao, 2026-10-08: on a default install Arturo could not pair at all, because nothing set
ORCHESTRA_PUBLIC_URL; the address `tailscale serve` already serves the gateway on was never read.
"""
import json
import subprocess
import types

import pytest

from scripts import public_url as pu


def _serve(**web):
    return {"TCP": {}, "Web": {hp: {"Handlers": h} for hp, h in web.items()}, "AllowFunnel": {}}


GW = 8890


def test_the_gateways_serve_entry_is_found():
    doc = _serve(**{"box.tn.ts.net:8445": {"/": {"Proxy": "http://127.0.0.1:8890"}},
                    "box.tn.ts.net:443": {"/": {"Proxy": "http://127.0.0.1:5053"}}})
    assert pu.serve_candidates(doc, GW) == ["https://box.tn.ts.net:8445"]


@pytest.mark.parametrize("proxy", ["http://localhost:8890", "http://[::1]:8890", "http://127.0.0.1:8890/"])
def test_every_loopback_spelling_counts(proxy):
    assert pu.serve_candidates(_serve(**{"b.ts.net:8445": {"/": {"Proxy": proxy}}}), GW) == ["https://b.ts.net:8445"]


def test_port_443_is_the_bare_host():
    assert pu.serve_candidates(_serve(**{"b.ts.net:443": {"/": {"Proxy": "http://127.0.0.1:8890"}}}), GW) == ["https://b.ts.net"]


@pytest.mark.parametrize("handlers", [
    {"/": {"Proxy": "http://127.0.0.1:8891"}},              # the dashboard, not the gateway
    {"/arturo": {"Proxy": "http://127.0.0.1:8890"}},        # a sub-path is not the gateway's root
    {"/": {"Proxy": "http://10.0.0.5:8890"}},               # not this machine
    {"/": {"Path": "/var/www"}},                            # a file server
])
def test_anything_else_is_not_the_gateway(handlers):
    assert pu.serve_candidates(_serve(**{"b.ts.net:8445": handlers}), GW) == []


def _run(stdout="", rc=0, exc=None):
    def run(argv, **kw):
        assert argv == ["tailscale", "serve", "status", "--json"] and kw.get("timeout") == pu.SERVE_TIMEOUT_S
        if exc:
            raise exc
        return types.SimpleNamespace(returncode=rc, stdout=stdout)
    return run


@pytest.mark.parametrize("run", [
    _run(exc=FileNotFoundError("tailscale")),                         # not installed
    _run(exc=subprocess.TimeoutExpired("tailscale", 3)),              # a stuck daemon
    _run(rc=1, stdout="not logged in"),                               # logged out
    _run(stdout="{not json"),
    _run(stdout="null"),
])
def test_no_usable_tailscale_means_nothing_found_never_a_crash(run):
    assert pu.detect(GW, run=run) == []


def test_the_order_is_explicit_env_config_tailscale():
    serve = _run(stdout=json.dumps(_serve(**{"t.ts.net:8445": {"/": {"Proxy": "http://127.0.0.1:8890"}}})))
    env = {"ORCHESTRA_PUBLIC_URL": "https://env.ts.net:8445"}
    assert pu.resolve("https://cli.ts.net:1", env, "https://cfg.ts.net:2", GW, serve).url == "https://cli.ts.net:1"
    assert pu.resolve(None, env, "https://cfg.ts.net:2", GW, serve).source == "env"
    assert pu.resolve(None, {}, "https://cfg.ts.net:2", GW, serve).url == "https://cfg.ts.net:2"
    got = pu.resolve(None, {}, "", GW, serve)
    assert (got.url, got.source) == ("https://t.ts.net:8445", "tailscale")


@pytest.mark.parametrize("bad", ["box.ts.net:8445", "http://box:8445", "https://"])
def test_a_given_address_that_is_not_https_is_refused_not_skipped(bad):
    got = pu.resolve(bad, {}, "", GW, _run(exc=AssertionError("must not fall through to tailscale")))
    assert got.url is None and "https" in got.problem


def test_two_serve_entries_for_the_gateway_are_a_refusal_that_lists_both():
    doc = _serve(**{"a.ts.net:8445": {"/": {"Proxy": "http://127.0.0.1:8890"}},
                    "a.ts.net:9445": {"/": {"Proxy": "http://localhost:8890"}}})
    got = pu.resolve(None, {}, "", GW, _run(stdout=json.dumps(doc)))
    assert got.url is None and "https://a.ts.net:8445" in got.problem and "https://a.ts.net:9445" in got.problem


def test_nothing_found_says_exactly_what_to_do():
    got = pu.resolve(None, {}, "", GW, _run(rc=1))
    assert got.url is None
    assert "tailscale serve --bg --https=8445 http://127.0.0.1:8890" in got.problem and "public_url" in got.problem
