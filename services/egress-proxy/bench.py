#!/usr/bin/env python3
"""Local benchmark: per-request overhead and CONNECT setup time of the egress proxy against a
direct connection. Everything runs on this host: a small upstream on 127.0.0.2 (let through by
the test-only override), the proxy on 127.0.0.1 and an in-process resolver, so DNS time is
not part of the numbers."""

import argparse
import asyncio
import statistics
import time

import egress_proxy as ep

UPSTREAM = "127.0.0.2"
BODY = b"x" * 1024


async def upstream_handler(reader, writer):
    try:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                     % (len(BODY), BODY))
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()


async def timed_get(host, port, request):
    t0 = time.perf_counter()
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(request)
    data = await reader.read()
    elapsed = time.perf_counter() - t0
    writer.close()
    assert data.startswith(b"HTTP/1.1 200") and data.endswith(BODY), data[:80]
    return elapsed


async def timed_connect_direct(host, port):
    t0 = time.perf_counter()
    _, writer = await asyncio.open_connection(host, port)
    elapsed = time.perf_counter() - t0
    writer.close()
    return elapsed


async def timed_connect_proxy(pport, authority):
    t0 = time.perf_counter()
    reader, writer = await asyncio.open_connection("127.0.0.1", pport)
    writer.write(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
    head = await reader.readuntil(b"\r\n\r\n")
    elapsed = time.perf_counter() - t0
    writer.close()
    assert head.startswith(b"HTTP/1.1 200"), head
    return elapsed


def summary(samples):
    ms = sorted(s * 1000 for s in samples)
    return statistics.median(ms), ms[int(len(ms) * 0.95) - 1]


async def main(n):
    up = await asyncio.start_server(upstream_handler, UPSTREAM, 0)
    uport = up.sockets[0].getsockname()[1]

    async def resolver(host, port):
        return [UPSTREAM]

    proxy = ep.EgressProxy(ep.Policy(test_allow=[UPSTREAM], testing=True), resolver=resolver)
    server = await proxy.start(0)
    pport = server.sockets[0].getsockname()[1]
    direct_req = "GET / HTTP/1.1\r\nHost: bench.test\r\n\r\n".encode()
    proxy_req = f"GET http://bench.test:{uport}/ HTTP/1.1\r\nHost: bench.test\r\n\r\n".encode()

    for _ in range(50):  # warm up
        await timed_get(UPSTREAM, uport, direct_req)
        await timed_get("127.0.0.1", pport, proxy_req)

    rows = []
    for label, fn in [
        ("GET direct", lambda: timed_get(UPSTREAM, uport, direct_req)),
        ("GET via proxy", lambda: timed_get("127.0.0.1", pport, proxy_req)),
        ("TCP connect direct", lambda: timed_connect_direct(UPSTREAM, uport)),
        ("CONNECT via proxy", lambda: timed_connect_proxy(pport, f"bench.test:{uport}")),
    ]:
        rows.append((label, *summary([await fn() for _ in range(n)])))

    server.close()
    up.close()
    print(f"{n} sequential samples each, 1 KiB response, loopback only")
    print(f"{'':22}{'median ms':>10}{'p95 ms':>10}")
    for label, med, p95 in rows:
        print(f"{label:22}{med:10.3f}{p95:10.3f}")
    print(f"per-request overhead (median): {rows[1][1] - rows[0][1]:.3f} ms")
    print(f"CONNECT setup overhead (median): {rows[3][1] - rows[2][1]:.3f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=1000)
    asyncio.run(main(ap.parse_args().n))
