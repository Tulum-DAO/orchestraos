#!/usr/bin/env python3
"""Public-only egress proxy for agent browsers.

A forward proxy bound to 127.0.0.1 that only ever opens connections to public internet
addresses. It handles plain HTTP requests, CONNECT tunnels (HTTPS, wss) and WebSocket
upgrades. A headless browser pointed at it cannot be steered by a hostile page to loopback
services, the tailnet, private networks or cloud metadata.

How a request is decided:
  1. The URL (or CONNECT authority) is parsed; credentials in it and any scheme other than
     http, https, ws and wss are refused.
  2. The hostname is resolved ONCE, at connect time. If ANY resolved address is not public,
     the whole request is refused.
  3. The upstream connection goes to an address from that same lookup, never to a second
     lookup of the name, so a DNS answer that changes between check and connect (rebinding)
     cannot redirect it.
  4. A refusal is a 403 whose body names the reason and nothing else.

Stdlib only (Python 3.12, asyncio). Run: python3 egress_proxy.py --port 3128
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import socket
import ssl
import sys
from typing import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

DEFAULT_PORT = 3128
LISTEN_HOST = "127.0.0.1"
MAX_HEAD_BYTES = 64 * 1024
HEAD_TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 300.0
ALLOWED_SCHEMES = {"http": 80, "https": 443, "ws": 80, "wss": 443}

REFUSED_V4 = tuple(ipaddress.IPv4Network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    "255.255.255.255/32",
))
REFUSED_V6 = tuple(ipaddress.IPv6Network(n) for n in (
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "2001:db8::/32",
))
# IPv6 forms that carry an IPv4 address in their low 32 bits: judged by that IPv4 address.
EMBEDS_V4 = tuple(ipaddress.IPv6Network(n) for n in ("::ffff:0:0/96", "64:ff9b::/96"))
SIX_TO_FOUR = ipaddress.IPv6Network("2002::/16")  # 6to4 carries IPv4 in bits 16..47

# Set by main(): once the process is running as the real proxy, the test override is refused.
_PRODUCTION = False

log = logging.getLogger("egress-proxy")

Resolver = Callable[[str, int], Awaitable[list[str]]]


def _strip_zone(addr: str) -> str:
    return addr.split("%", 1)[0]


def _v4_refused(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in net for net in REFUSED_V4)


def is_public(addr: str) -> bool:
    """True when `addr` (an IP literal) is a public address this proxy may connect to."""
    ip = ipaddress.ip_address(_strip_zone(addr))
    if ip.version == 4:
        return not _v4_refused(ip)
    if any(ip in net for net in REFUSED_V6):
        return False
    if any(ip in net for net in EMBEDS_V4):
        return not _v4_refused(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if ip in SIX_TO_FOUR:
        return not _v4_refused(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    return True


class Refused(Exception):
    """The request is not allowed. str(exc) is the reason shown to the client."""


class BadRequest(Exception):
    pass


class UpstreamError(Exception):
    def __init__(self, status: str, body: str):
        super().__init__(body)
        self.status, self.body = status, body


class Policy:
    """Which destination addresses are allowed.

    `test_allow` lets a test reach a fake upstream on a loopback address. It needs the
    explicit `testing=True` flag and is refused outright once main() has started the real
    proxy; nothing reads it from the environment or the command line.
    """

    def __init__(self, *, test_allow: Iterable[str] = (), testing: bool = False):
        allow = frozenset(_strip_zone(a) for a in test_allow)
        if allow and not testing:
            raise ValueError("test_allow needs testing=True")
        if allow and _PRODUCTION:
            raise RuntimeError("the test override cannot be enabled in the running proxy")
        self._test_allow = allow

    def check(self, addrs: list[str]) -> None:
        for addr in addrs:
            if _strip_zone(addr) in self._test_allow:
                continue
            try:
                public = is_public(addr)
            except ValueError:  # an answer that is not an IP address at all
                public = False
            if not public:
                raise Refused(f"destination {addr} is not a public address")


async def system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr[0] not in seen:
            seen.append(sockaddr[0])
    return seen


def _host_port(authority: str, default_port: int) -> tuple[str, int]:
    """Split host[:port] (IPv6 in brackets). Refuses credentials."""
    if "@" in authority:
        raise Refused("URLs carrying credentials are not allowed")
    parts = urlsplit("//" + authority)
    try:
        port = parts.port or default_port
    except ValueError:
        raise BadRequest("invalid port") from None
    if not parts.hostname or not 0 < port < 65536:
        raise BadRequest("invalid destination")
    return parts.hostname, port


def _response(status: str, body: str) -> bytes:
    data = (body + "\n").encode()
    return (f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data


HOP_BY_HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authorization",
              "proxy-authenticate", "te", "trailer", "upgrade"}


class EgressProxy:
    def __init__(self, policy: Policy | None = None, resolver: Resolver = system_resolve,
                 connect_timeout: float = CONNECT_TIMEOUT, idle_timeout: float = IDLE_TIMEOUT):
        self.policy = policy or Policy()
        self.resolver = resolver
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout

    async def start(self, port: int = DEFAULT_PORT) -> asyncio.Server:
        return await asyncio.start_server(self._handle, LISTEN_HOST, port, limit=MAX_HEAD_BYTES)

    async def resolve_checked(self, host: str, port: int) -> list[str]:
        """Resolve once and refuse unless every address is allowed. Returns the addresses."""
        try:
            ipaddress.ip_address(_strip_zone(host))
            addrs = [host]
        except ValueError:
            try:
                addrs = await self.resolver(host, port)
            except OSError:
                addrs = []
            if not addrs:
                raise UpstreamError("502 Bad Gateway", f"could not resolve {host}")
        self.policy.check(addrs)
        return addrs

    async def open_upstream(self, addr: str, port: int, tls_host: str | None):
        kwargs = {}
        if tls_host:
            kwargs = {"ssl": ssl.create_default_context(), "server_hostname": tls_host}
        return await asyncio.open_connection(_strip_zone(addr), port, **kwargs)

    async def _connect_checked(self, host: str, port: int, tls: bool):
        addrs = await self.resolve_checked(host, port)
        for addr in addrs:
            try:
                return await asyncio.wait_for(
                    self.open_upstream(addr, port, host if tls else None), self.connect_timeout)
            except OSError:  # includes TimeoutError and ssl.SSLError
                continue
        raise UpstreamError("502 Bad Gateway", f"could not connect to {host}:{port}")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream = None
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    asyncio.TimeoutError, ConnectionError):
                return
            try:
                method, target, version, headers = _parse_head(head)
                if method == "CONNECT":
                    host, port = _host_port(target, 443)
                    upstream = await self._connect_checked(host, port, tls=False)
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    upstream = await self._forward_request(method, target, version, headers)
            except Refused as exc:
                log.info("refused: %s", exc)
                writer.write(_response("403 Forbidden", f"refused: {exc}"))
                return
            except BadRequest as exc:
                writer.write(_response("400 Bad Request", f"bad request: {exc}"))
                return
            except UpstreamError as exc:
                writer.write(_response(exc.status, exc.body))
                return
            await self._relay(reader, writer, *upstream)
        finally:
            for w in (writer, upstream[1] if upstream else None):
                if w is not None:
                    w.close()
                    try:
                        await w.wait_closed()
                    except OSError:
                        pass

    async def _forward_request(self, method, target, version, headers):
        url = urlsplit(target)
        scheme = url.scheme.lower()
        if not scheme:
            raise BadRequest("a proxy request needs an absolute URL")
        if scheme not in ALLOWED_SCHEMES:
            raise Refused(f"scheme {scheme} is not allowed")
        host, port = _host_port(url.netloc, ALLOWED_SCHEMES[scheme])
        upgrade = _is_upgrade(headers)
        out = [f"{method} {(url.path or '/') + ('?' + url.query if url.query else '')} {version}"]
        has_host = False
        for name, value in headers:
            low = name.lower()
            if low in HOP_BY_HOP:
                continue
            has_host |= low == "host"
            out.append(f"{name}: {value}")
        if not has_host:
            out.append(f"Host: {url.netloc}")
        if upgrade:
            out += ["Connection: Upgrade", f"Upgrade: {_header(headers, 'upgrade')}"]
        else:
            out.append("Connection: close")  # one request per upstream connection
        reader, writer = await self._connect_checked(host, port, tls=scheme in ("https", "wss"))
        writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
        return reader, writer

    async def _relay(self, c_reader, c_writer, u_reader, u_writer) -> None:
        down = asyncio.create_task(self._pipe(u_reader, c_writer))
        up = asyncio.create_task(self._pipe(c_reader, u_writer))
        try:
            await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
            if up.done() and not down.done():
                await down  # client finished sending; let the response finish
        finally:
            for t in (down, up):
                t.cancel()
            await asyncio.gather(down, up, return_exceptions=True)

    async def _pipe(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while data := await asyncio.wait_for(src.read(65536), self.idle_timeout):
                dst.write(data)
                await dst.drain()
            if dst.can_write_eof():
                dst.write_eof()
        except OSError:  # includes ConnectionError and TimeoutError
            pass


def _parse_head(head: bytes):
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ")
    except ValueError:
        raise BadRequest("malformed request line") from None
    if not version.startswith("HTTP/1."):
        raise BadRequest("unsupported HTTP version")
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip():
            raise BadRequest("malformed header")
        headers.append((name, value.strip()))
    return method.upper(), target, version, headers


def _header(headers, name: str) -> str:
    return next((v for n, v in headers if n.lower() == name), "")


def _is_upgrade(headers) -> bool:
    conn = {t.strip().lower() for t in _header(headers, "connection").split(",")}
    return "upgrade" in conn and bool(_header(headers, "upgrade"))


async def check_host(host: str, policy: Policy) -> str:
    """Resolve `host` the way the proxy would and return 'allowed' or the refusal."""
    proxy = EgressProxy(policy)
    try:
        addrs = await proxy.resolve_checked(host, 443)
    except Refused as exc:
        return f"refused: {exc}"
    except UpstreamError as exc:
        return exc.body
    return "allowed: " + ", ".join(addrs)


def main(argv: list[str] | None = None) -> int:
    global _PRODUCTION
    _PRODUCTION = True
    ap = argparse.ArgumentParser(description="Public-only egress proxy for agent browsers.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"port on {LISTEN_HOST} (default {DEFAULT_PORT}; 0 picks a free one)")
    ap.add_argument("--check", metavar="HOST",
                    help="print whether HOST would be allowed, then exit (1 when refused)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    policy = Policy()
    if args.check:
        verdict = asyncio.run(check_host(args.check, policy))
        print(verdict)
        return 0 if verdict.startswith("allowed") else 1

    async def serve() -> None:
        server = await EgressProxy(policy).start(args.port)
        port = server.sockets[0].getsockname()[1]
        print(f"egress proxy listening on {LISTEN_HOST}:{port}", flush=True)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
