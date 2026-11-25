# Public-only egress proxy for agent browsers

A forward proxy bound to `127.0.0.1` that only ever connects to public internet addresses.
Point an agent's headless browser at it, and a hostile page can no longer steer that browser
to services on the host's loopback interface, the tailnet, private networks or cloud metadata
(`169.254.169.254`).

Python 3.12 standard library only (`asyncio`, `ipaddress`). It needs no dependencies and no
build step.

## Run it

```bash
python3 services/egress-proxy/egress_proxy.py --port 3128
# egress proxy listening on 127.0.0.1:3128

python3 services/egress-proxy/egress_proxy.py --check 100.100.100.100
# refused: destination 100.100.100.100 is not a public address   (exit 1)
```

It always listens on `127.0.0.1`, and no option changes that. Refusals are logged to stderr,
one line each.

## Pointing Chrome at it

Start Chrome (or Chromium, headless or not) with all four of these flags:

```
--proxy-server=http://127.0.0.1:3128
--proxy-bypass-list=<-loopback>
--disable-quic
--force-webrtc-ip-handling-policy=disable_non_proxied_udp
```

| Flag | Why |
|---|---|
| `--proxy-server=http://127.0.0.1:3128` | Sends HTTP, HTTPS (as `CONNECT`) and WebSocket traffic through the proxy. |
| `--proxy-bypass-list=<-loopback>` | By default Chrome connects to `localhost` and `127.0.0.1` directly and skips the proxy. `<-loopback>` removes that built-in exception, so loopback requests also go through the proxy, which refuses them. |
| `--disable-quic` | QUIC (HTTP/3) is UDP and would not go through an HTTP proxy. |
| `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` | Stops WebRTC from opening UDP paths that skip the proxy. |

Adding this proxy does not change any existing browser tool configuration. Wiring it in is a
separate step.

## What it decides, and how

1. **Scheme and credentials.** Only `http`, `https`, `ws` and `wss` URLs are proxied. A URL or
   `CONNECT` authority that carries credentials (`user:pass@host`, or `user@host`) is refused.
2. **One lookup, at connect time.** The hostname is resolved once, when the request arrives.
   An IP literal is used as it is.
3. **All answers must be public.** If any resolved address is not public, the whole request
   is refused, even when other addresses in the same answer are public.
4. **Connect to what was checked.** The upstream connection goes to an address from that same
   lookup. The name is never looked up a second time, so a DNS answer that changes between
   the check and the connection (DNS rebinding) cannot redirect it.
5. **A plain refusal.** A refused request gets `403 Forbidden` with a one-line body that names
   the reason and nothing more, for example:
   `refused: destination 100.100.100.100 is not a public address`.

Refused destinations:

- IPv4: `0.0.0.0/8`, `10.0.0.0/8`, `100.64.0.0/10` (this includes Tailscale), `127.0.0.0/8`,
  `169.254.0.0/16`, `172.16.0.0/12`, `192.0.0.0/24`, `192.0.2.0/24`, `192.88.99.0/24`,
  `192.168.0.0/16`, `198.18.0.0/15`, `198.51.100.0/24`, `203.0.113.0/24`, `224.0.0.0/4`,
  `240.0.0.0/4`, `255.255.255.255`.
- IPv6: `::`, `::1`, `fc00::/7`, `fe80::/10`, `ff00::/8`, `2001:db8::/32`.
- IPv6 forms that embed an IPv4 address are judged by that IPv4 address: IPv4-mapped
  (`::ffff:0:0/96`), NAT64 (`64:ff9b::/96`) and 6to4 (`2002::/16`).

Plain HTTP requests are rewritten to origin form, with proxy-only headers (`Proxy-Connection`,
`Proxy-Authorization`) stripped. Each request uses its own upstream connection. A WebSocket
upgrade keeps its `Upgrade` headers, and the proxy relays the connection in both directions
once the upstream answers `101`.

## Tests

```bash
python3 -m pytest -q services/egress-proxy
```

The tests never touch the internet. An injected resolver maps test hostnames to chosen
addresses, and the "public" upstream is a local fake on `127.0.0.2`. Only a test-only override
lets that address through. The override has three guards:

- it needs an explicit `testing=True` flag;
- it is refused once `main()` is running as the real proxy;
- it is never read from the environment or the command line.

A test runs the real proxy as a subprocess with likely-looking override variables set in its
environment, and checks that the proxy still refuses the address.

`bench.py` measures the per-request overhead and the `CONNECT` setup time against a direct
connection, all on the local host:

```bash
python3 services/egress-proxy/bench.py
```
