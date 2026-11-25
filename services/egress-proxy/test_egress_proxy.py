"""Tests for the public-only egress proxy. Nothing here touches the internet: hostnames are
mapped to chosen addresses by an injected resolver, and the "public" upstream is a local fake
on 127.0.0.2 that only the explicit test override lets through."""

import asyncio
import ipaddress
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import egress_proxy as ep

HERE = Path(__file__).resolve().parent
FAKE_PUBLIC = "127.0.0.2"   # Linux routes all of 127/8 to lo
DECOY = "127.0.0.1"         # stands in for the control plane
TIMEOUT = 5


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


# --------------------------------------------------------------------------------------------
# 1. Address classification

def _edges(cidr):
    net = ipaddress.ip_network(cidr)
    return sorted({str(net[0]), str(net[net.num_addresses // 2]), str(net[-1])})


REFUSED_CIDRS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    "255.255.255.255/32",
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "2001:db8::/32",
    # not public either, though not in the first list: IPv4-compatible, IPv4-translated,
    # local-use NAT64, site-local, discard-only, IETF protocol assignments (incl. Teredo), 6to4,
    # documentation (RFC 9637) and SRv6 SIDs
    "::/96", "::ffff:0:0:0/96", "64:ff9b:1::/48", "fec0::/10", "100::/64", "2001::/23",
    "2002::/16", "3fff::/20", "5f00::/16",
]


@pytest.mark.parametrize("addr", [a for c in REFUSED_CIDRS for a in _edges(c)])
def test_every_listed_range_is_refused(addr):
    assert not ep.is_public(addr)


@pytest.mark.parametrize("addr", [
    "1.1.1.1", "8.8.8.8", "93.184.216.34",
    "2606:4700:4700::1111", "2001:4860:4860::8888",
    # just outside the refused ranges, so no range is wider than listed
    "9.255.255.255", "11.0.0.0", "100.63.255.255", "100.128.0.0", "126.255.255.255",
    "128.0.0.0", "169.253.255.255", "169.255.0.0", "172.15.255.255", "172.32.0.0",
    "192.0.1.0", "192.88.98.255", "192.169.0.0", "198.17.255.255", "198.20.0.0",
    "223.255.255.255", "2001:db9::1", "2001:200::1", "2a00:1450:4001::1",
])
def test_public_addresses_are_allowed(addr):
    assert ep.is_public(addr)


@pytest.mark.parametrize("addr", [
    "::ffff:127.0.0.1", "::ffff:10.1.2.3", "::ffff:100.100.100.100", "::ffff:169.254.169.254",
    "::ffff:0.0.0.0", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "64:ff9b::6464:6464",
    "64:ff9b::c0a8:101", "2002:7f00:1::1", "2002:a9fe:a9fe::",
    "::127.0.0.1", "::ffff:0:127.0.0.1", "64:ff9b:1::7f00:1", "64:ff9b:1::101:101",
])
def test_v4_embedded_in_v6_is_judged_by_the_v4_address(addr):
    assert not ep.is_public(addr)


@pytest.mark.parametrize("addr", ["::ffff:1.1.1.1", "64:ff9b::101:101"])
def test_v4_embedded_public_address_is_allowed(addr):
    assert ep.is_public(addr)


def test_zone_id_does_not_hide_link_local():
    assert not ep.is_public("fe80::1%eth0")


def test_any_non_public_answer_refuses_the_whole_lookup():
    with pytest.raises(ep.Refused, match="destination 10.0.0.5 is not a public address"):
        ep.Policy().check(["1.1.1.1", "10.0.0.5"])
    ep.Policy().check(["1.1.1.1", "2606:4700:4700::1111"])


def test_an_answer_that_is_not_an_address_is_refused():
    with pytest.raises(ep.Refused):
        ep.Policy().check(["not-an-ip"])


# --------------------------------------------------------------------------------------------
# Test override: explicit, test-only, never on in the running proxy

def test_override_needs_the_explicit_testing_flag():
    with pytest.raises(ValueError):
        ep.Policy(test_allow=[FAKE_PUBLIC])


def test_override_is_refused_once_running_as_the_real_proxy(monkeypatch):
    monkeypatch.setattr(ep, "_PRODUCTION", True)
    with pytest.raises(RuntimeError):
        ep.Policy(test_allow=[FAKE_PUBLIC], testing=True)


OVERRIDE_ENV = {
    "EGRESS_PROXY_TEST_ALLOW": FAKE_PUBLIC, "EGRESS_PROXY_TESTING": "1",
    "EGRESS_PROXY_ALLOW": FAKE_PUBLIC, "TEST_ALLOW": FAKE_PUBLIC, "TESTING": "1",
}


def _prod_env():
    return {**os.environ, **OVERRIDE_ENV, "PYTHONUNBUFFERED": "1"}


def test_environment_cannot_enable_override_in_check_mode():
    out = subprocess.run([sys.executable, str(HERE / "egress_proxy.py"), "--check", FAKE_PUBLIC],
                         env=_prod_env(), capture_output=True, text=True, timeout=30)
    assert out.returncode == 1
    assert f"destination {FAKE_PUBLIC} is not a public address" in out.stdout


def test_environment_cannot_enable_override_in_the_running_proxy():
    target = socket.socket()
    target.bind((FAKE_PUBLIC, 0))
    target.listen()
    target.setblocking(False)
    proc = subprocess.Popen([sys.executable, str(HERE / "egress_proxy.py"), "--port", "0"],
                            env=_prod_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    try:
        line = proc.stdout.readline()
        assert "listening on 127.0.0.1:" in line, line
        port = int(line.rsplit(":", 1)[1])
        tport = target.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT) as c:
            c.sendall(f"GET http://{FAKE_PUBLIC}:{tport}/ HTTP/1.1\r\n"
                      f"Host: {FAKE_PUBLIC}:{tport}\r\n\r\n".encode())
            resp = _recv_all(c)
        assert resp.startswith(b"HTTP/1.1 403 ")
        assert f"destination {FAKE_PUBLIC} is not a public address".encode() in resp
        with pytest.raises(BlockingIOError):
            target.accept()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        target.close()


def _recv_all(sock):
    chunks = []
    while chunk := sock.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------------------------
# Harness: fake public upstream, decoy control plane, resolver, spying proxy

class FakeUpstream:
    """HTTP server on 127.0.0.2: answers requests, accepts WebSocket upgrades and echoes."""

    def __init__(self, host=FAKE_PUBLIC, port=0):
        self.host, self.port, self.heads, self.connections = host, port, [], 0

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        self.connections += 1
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            self.heads.append(head)
            if "upgrade: websocket" in head.lower():
                writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                             b"Connection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n")
                while data := await reader.read(65536):
                    writer.write(b"echo:" + data)
                    await writer.drain()
            else:
                body = f"upstream ok {head.split()[1]}".encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s"
                             % (len(body), body))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


class Resolver:
    def __init__(self, table):
        self.table, self.calls = table, []

    async def __call__(self, host, port):
        self.calls.append(host)
        answer = self.table[host]
        return answer(len(self.calls)) if callable(answer) else list(answer)


class SpyProxy(ep.EgressProxy):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.opened = []

    async def open_upstream(self, addr, port, tls_host):
        self.opened.append((addr, port))
        return await super().open_upstream(addr, port, tls_host)


async def start_proxy(table, test_allow=(FAKE_PUBLIC,)):
    resolver = Resolver(table)
    proxy = SpyProxy(ep.Policy(test_allow=test_allow, testing=True), resolver=resolver,
                     connect_timeout=2)
    server = await proxy.start(0)
    return proxy, resolver, server, server.sockets[0].getsockname()[1]


async def exchange(port, raw):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), TIMEOUT)
    writer.close()
    return data


def get(host, path="/", extra=""):
    return f"GET http://{host}{path} HTTP/1.1\r\nHost: {host}\r\n{extra}\r\n".encode()


TABLE = {
    "loopback.test": ["127.0.0.1"],
    "tailnet.test": ["100.100.100.100"],
    "metadata.test": ["169.254.169.254"],
    "mixed.test": [FAKE_PUBLIC, "10.0.0.1"],
    "mapped.test": ["::ffff:127.0.0.1"],
    "public.test": [FAKE_PUBLIC],
}
PRIVATE = [("loopback.test", "127.0.0.1"), ("tailnet.test", "100.100.100.100"),
           ("metadata.test", "169.254.169.254"), ("mixed.test", "10.0.0.1"),
           ("mapped.test", "::ffff:127.0.0.1")]


def test_proxy_listens_on_loopback_only():
    async def go():
        server = await ep.EgressProxy().start(0)
        addrs = [s.getsockname()[0] for s in server.sockets]
        server.close()
        return addrs
    assert run(go()) == ["127.0.0.1"]


# --------------------------------------------------------------------------------------------
# 2. Rebinding: connect to the address that was checked, never a second lookup

def test_rebinding_connects_to_the_checked_address():
    async def go():
        async with FakeUpstream() as up, FakeUpstream(DECOY, up.port) as decoy:
            # the decoy sits on the same port at 127.0.0.1: a second lookup would reach it
            table = {"rebind.test": lambda n: [FAKE_PUBLIC] if n == 1 else [DECOY]}
            proxy, resolver, server, port = await start_proxy(table)
            resp = await exchange(port, get(f"rebind.test:{up.port}", "/r"))
            resp2 = await exchange(port, f"CONNECT rebind.test:{up.port} HTTP/1.1\r\n\r\n"
                                   .encode())
            server.close()
            return resp, resp2, resolver.calls, proxy.opened, up.connections, decoy.connections
    resp, resp2, calls, opened, up_conns, decoy_conns = run(go())
    assert resp.startswith(b"HTTP/1.1 200 ") and resp.endswith(b"upstream ok /r")
    assert opened[0][0] == FAKE_PUBLIC
    assert calls[0] == "rebind.test" and len(calls) == 2  # one lookup per request, no more
    # the second request's lookup answered 127.0.0.1: refused, never connected
    assert resp2.startswith(b"HTTP/1.1 403 ")
    assert len(opened) == 1 and up_conns == 1 and decoy_conns == 0


# --------------------------------------------------------------------------------------------
# 3. End to end: private destinations get 403, the fake public upstream succeeds

@pytest.mark.parametrize("host,addr", PRIVATE)
def test_http_to_private_destination_is_refused(host, addr):
    async def go():
        async with FakeUpstream(DECOY) as decoy:
            proxy, _, server, port = await start_proxy(TABLE)
            resp = await exchange(port, get(f"{host}:{decoy.port}"))
            server.close()
            return resp, proxy.opened, decoy.connections
    resp, opened, decoy_conns = run(go())
    assert resp.startswith(b"HTTP/1.1 403 Forbidden\r\n")
    assert resp.endswith(f"\r\n\r\nrefused: destination {addr} is not a public address\n"
                         .encode())
    assert opened == [] and decoy_conns == 0


@pytest.mark.parametrize("literal", ["127.0.0.1", "[::1]", "169.254.169.254", "100.100.100.100",
                                     "[::127.0.0.1]", "[::ffff:0:127.0.0.1]",
                                     "[64:ff9b:1::7f00:1]", "[fec0::1]"])
def test_http_to_private_ip_literal_is_refused(literal):
    async def go():
        proxy, resolver, server, port = await start_proxy(TABLE)
        resp = await exchange(port, get(f"{literal}:8888"))
        server.close()
        return resp, proxy.opened, resolver.calls
    resp, opened, calls = run(go())
    assert resp.startswith(b"HTTP/1.1 403 ") and b"is not a public address" in resp
    assert opened == [] and calls == []


def test_http_to_public_destination_succeeds():
    async def go():
        async with FakeUpstream() as up:
            proxy, _, server, port = await start_proxy(TABLE)
            resp = await exchange(port, get(f"public.test:{up.port}", "/hello?x=1",
                                            "Proxy-Connection: keep-alive\r\n"
                                            "Proxy-Authorization: Basic Zm9vOmJhcg==\r\n"))
            server.close()
            return resp, up.heads
    resp, heads = run(go())
    assert resp.startswith(b"HTTP/1.1 200 OK") and resp.endswith(b"upstream ok /hello?x=1")
    head = heads[0].lower()
    assert heads[0].startswith("GET /hello?x=1 HTTP/1.1\r\n")
    assert "proxy-connection" not in head and "proxy-authorization" not in head
    assert "connection: close" in head


def test_request_body_is_forwarded():
    async def go():
        got = []

        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            got.append(await reader.readexactly(5))
            writer.write(b"HTTP/1.1 204 No Content\r\n\r\n")
            writer.close()
        up = await asyncio.start_server(handle, FAKE_PUBLIC, 0)
        uport = up.sockets[0].getsockname()[1]
        _, _, server, port = await start_proxy(TABLE)
        resp = await exchange(port, f"POST http://public.test:{uport}/ HTTP/1.1\r\n"
                              f"Host: public.test\r\nContent-Length: 5\r\n\r\nhello".encode())
        server.close()
        up.close()
        return resp, got
    resp, got = run(go())
    assert resp.startswith(b"HTTP/1.1 204") and got == [b"hello"]


def test_unresolvable_host_is_a_502_without_detail():
    async def go():
        async def nothing(host, port):
            raise socket.gaierror("no such host")
        proxy = ep.EgressProxy(resolver=nothing)
        server = await proxy.start(0)
        resp = await exchange(server.sockets[0].getsockname()[1], get("nowhere.test"))
        server.close()
        return resp
    resp = run(go())
    assert resp.startswith(b"HTTP/1.1 502 ") and resp.endswith(b"could not resolve nowhere.test\n")


def test_origin_form_request_is_a_400():
    async def go():
        _, _, server, port = await start_proxy(TABLE)
        resp = await exchange(port, b"GET / HTTP/1.1\r\nHost: public.test\r\n\r\n")
        server.close()
        return resp
    assert run(go()).startswith(b"HTTP/1.1 400 ")


# --------------------------------------------------------------------------------------------
# 4. CONNECT and WebSocket upgrade follow the same rules

async def open_tunnel(port, authority):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), TIMEOUT)
    return reader, writer, head


def test_connect_to_public_destination_tunnels():
    async def go():
        async with FakeUpstream() as up:
            proxy, _, server, port = await start_proxy(TABLE)
            reader, writer, head = await open_tunnel(port, f"public.test:{up.port}")
            writer.write(b"GET /through-tunnel HTTP/1.1\r\nHost: public.test\r\n\r\n")
            body = await asyncio.wait_for(reader.read(), TIMEOUT)
            writer.close()
            server.close()
            return head, body, proxy.opened
    head, body, opened = run(go())
    assert head.startswith(b"HTTP/1.1 200 ")
    assert body.endswith(b"upstream ok /through-tunnel")
    assert opened[0][0] == FAKE_PUBLIC


@pytest.mark.parametrize("host,addr", PRIVATE)
def test_connect_to_private_destination_is_refused(host, addr):
    async def go():
        async with FakeUpstream(DECOY) as decoy:
            proxy, _, server, port = await start_proxy(TABLE)
            resp = await exchange(port, f"CONNECT {host}:{decoy.port} HTTP/1.1\r\n\r\n"
                                  .encode())
            server.close()
            return resp, proxy.opened, decoy.connections
    resp, opened, decoy_conns = run(go())
    assert resp.startswith(b"HTTP/1.1 403 ")
    assert f"destination {addr} is not a public address".encode() in resp
    assert opened == [] and decoy_conns == 0


@pytest.mark.parametrize("authority", ["127.0.0.1:8888", "[::1]:9091", "169.254.169.254:80"])
def test_connect_to_private_ip_literal_is_refused(authority):
    async def go():
        proxy, _, server, port = await start_proxy(TABLE)
        resp = await exchange(port, f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
        server.close()
        return resp, proxy.opened
    resp, opened = run(go())
    assert resp.startswith(b"HTTP/1.1 403 ") and opened == []


WS_HEADERS = ("Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
              "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n")


def test_websocket_upgrade_to_public_destination_relays_both_ways():
    async def go():
        async with FakeUpstream() as up:
            _, _, server, port = await start_proxy(TABLE)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            host = f"public.test:{up.port}"
            writer.write(f"GET ws://{host}/ws HTTP/1.1\r\nHost: {host}\r\n{WS_HEADERS}\r\n"
                         .encode())
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), TIMEOUT)
            writer.write(b"ping")
            echo = await asyncio.wait_for(reader.readexactly(9), TIMEOUT)
            writer.close()
            server.close()
            return head, echo, up.heads
    head, echo, heads = run(go())
    assert head.startswith(b"HTTP/1.1 101 ") and echo == b"echo:ping"
    assert "upgrade: websocket" in heads[0].lower() and "connection: upgrade" in heads[0].lower()


@pytest.mark.parametrize("host,addr", PRIVATE)
def test_websocket_upgrade_to_private_destination_is_refused(host, addr):
    async def go():
        async with FakeUpstream(DECOY) as decoy:
            proxy, _, server, port = await start_proxy(TABLE)
            h = f"{host}:{decoy.port}"
            resp = await exchange(port, f"GET ws://{h}/ HTTP/1.1\r\nHost: {h}\r\n{WS_HEADERS}\r\n"
                                  .encode())
            server.close()
            return resp, proxy.opened, decoy.connections
    resp, opened, decoy_conns = run(go())
    assert resp.startswith(b"HTTP/1.1 403 ")
    assert f"destination {addr} is not a public address".encode() in resp
    assert opened == [] and decoy_conns == 0


# --------------------------------------------------------------------------------------------
# 5. Credentials and schemes

@pytest.mark.parametrize("raw", [
    get("user:pass@public.test:80"),
    get("user@public.test:80"),
    b"CONNECT user:pass@public.test:443 HTTP/1.1\r\n\r\n",
    b"GET ws://u:p@public.test/ HTTP/1.1\r\nHost: public.test\r\n\r\n",
])
def test_credentials_in_url_are_refused(raw):
    async def go():
        proxy, resolver, server, port = await start_proxy(TABLE)
        resp = await exchange(port, raw)
        server.close()
        return resp, proxy.opened, resolver.calls
    resp, opened, calls = run(go())
    assert resp.startswith(b"HTTP/1.1 403 ")
    assert resp.endswith(b"refused: URLs carrying credentials are not allowed\n")
    assert opened == [] and calls == []


@pytest.mark.parametrize("url", ["ftp://public.test/x", "file:///etc/passwd",
                                 "gopher://public.test:70/", "javascript:alert(1)"])
def test_other_schemes_are_refused(url):
    async def go():
        proxy, _, server, port = await start_proxy(TABLE)
        resp = await exchange(port, f"GET {url} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        server.close()
        return resp, proxy.opened
    resp, opened = run(go())
    assert resp.startswith(b"HTTP/1.1 403 ") and b"is not allowed" in resp and opened == []


# --------------------------------------------------------------------------------------------
# Other spellings of a refused address, through the real system resolver (numeric forms and
# /etc/hosts only, so still offline)

@pytest.mark.parametrize("host", [
    "0x7f000001", "2130706433", "127.1", "0177.0.0.1", "0x7f.0.0.1", "0", "localhost.",
    "LOCALHOST", "0251.0376.0251.0376", "0x64646464", "::ffff:7f00:1",
])
def test_other_spellings_of_a_refused_address_are_refused(host):
    verdict = run(ep.check_host(host, ep.Policy()))
    assert verdict.startswith("refused: destination ") and "is not a public address" in verdict


# --------------------------------------------------------------------------------------------
# Redirects are not followed by the proxy: the browser's follow-up request is checked afresh

def test_redirect_to_a_private_address_is_passed_back_and_the_follow_up_refused():
    async def go():
        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/\r\n"
                         b"Content-Length: 0\r\n\r\n")
            writer.close()
        up = await asyncio.start_server(handle, FAKE_PUBLIC, 0)
        uport = up.sockets[0].getsockname()[1]
        proxy, _, server, port = await start_proxy(TABLE)
        first = await exchange(port, get(f"public.test:{uport}"))
        follow = await exchange(port, get("169.254.169.254", "/latest/"))
        server.close()
        up.close()
        return first, follow, proxy.opened
    first, follow, opened = run(go())
    assert first.startswith(b"HTTP/1.1 302 ") and b"Location: http://169.254.169.254/" in first
    assert follow.startswith(b"HTTP/1.1 403 ")
    assert [a for a, _ in opened] == [FAKE_PUBLIC]


# --------------------------------------------------------------------------------------------
# Malformed input is a 400, never an unanswered connection or a forwarded injection

def test_non_ascii_host_is_a_400():
    async def go():
        server = await ep.EgressProxy().start(0)   # the real system resolver
        raw = "GET http://127\u30020\u30020\u30021:80/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        resp = await exchange(server.sockets[0].getsockname()[1], raw)
        server.close()
        return resp
    resp = run(go())
    assert resp.startswith(b"HTTP/1.1 400 ")


def test_a_resolver_that_rejects_the_name_is_a_400():
    async def go():
        async def bad(host, port):
            raise UnicodeError("label too long")
        server = await ep.EgressProxy(resolver=bad).start(0)
        resp = await exchange(server.sockets[0].getsockname()[1], get("x" * 70 + ".test"))
        server.close()
        return resp
    assert run(go()).startswith(b"HTTP/1.1 400 ")


def test_headers_named_by_connection_are_not_forwarded():
    async def go():
        async with FakeUpstream() as up:
            _, _, server, port = await start_proxy(TABLE)
            resp = await exchange(port, get(f"public.test:{up.port}", "/",
                                            "Connection: keep-alive, X-Hop-Only\r\n"
                                            "X-Hop-Only: secret\r\nX-End-To-End: kept\r\n"))
            server.close()
            return resp, up.heads
    resp, heads = run(go())
    assert resp.startswith(b"HTTP/1.1 200 ")
    assert "x-hop-only" not in heads[0].lower() and "X-End-To-End: kept" in heads[0]


@pytest.mark.parametrize("bad", ["X-A: a\nX-Injected: 1", "X-A: a\rX-Injected: 1",
                                 "X-A\n: a", "X-A: a\x00"])
def test_bare_cr_lf_or_nul_in_a_header_is_a_400(bad):
    async def go():
        async with FakeUpstream() as up:
            proxy, _, server, port = await start_proxy(TABLE)
            resp = await exchange(port, get(f"public.test:{up.port}", "/", bad + "\r\n"))
            server.close()
            return resp, proxy.opened, up.connections
    resp, opened, conns = run(go())
    assert resp.startswith(b"HTTP/1.1 400 ") and opened == [] and conns == 0


def test_an_unexpected_error_still_gets_an_answer():
    async def go():
        async def broken(host, port):
            raise RuntimeError("boom")
        server = await ep.EgressProxy(resolver=broken).start(0)
        resp = await exchange(server.sockets[0].getsockname()[1], get("x.test"))
        server.close()
        return resp
    resp = run(go())
    assert resp.startswith(b"HTTP/1.1 502 ") and resp.endswith(b"\r\n\r\nproxy error\n")


def test_a_non_ascii_name_is_refused_before_any_lookup():
    async def go():
        table = {"bücher.test": [FAKE_PUBLIC], "xn--bcher-kva.test": [FAKE_PUBLIC]}
        proxy, resolver, server, port = await start_proxy(table)
        raw = "GET http://bücher.test/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        resp = await exchange(port, raw)
        server.close()
        return resp, resolver.calls, proxy.opened
    resp, calls, opened = run(go())
    assert resp.startswith(b"HTTP/1.1 400 ") and calls == [] and opened == []
