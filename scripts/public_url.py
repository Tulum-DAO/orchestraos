"""The https address a phone, iPad or Mac uses to reach this install's gateway.

ONE answer for `orchestra pair` and for Arturo's pair_device, found in this order:
  a. what the caller passed explicitly (`orchestra pair --base-url`);
  b. ORCHESTRA_PUBLIC_URL in the environment;
  c. `[gateway] public_url` in orchestra.toml;
  d. `tailscale serve status --json`: the https address whose "/" is served from the gateway's own
     port, which is exactly what the onboarding's `tailscale serve` step creates.
Never a guess: two candidates in (d) is a refusal that lists both, and every answer must be an https
address with a host (scripts/pairing.valid_base_url), or the app could not pair with it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

SERVE_TIMEOUT_S = 3
_LOOPBACK_PROXY = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\]):(\d+)/?$")


@dataclass
class Resolution:
    url: str | None
    source: str            # "explicit" | "env" | "config" | "tailscale" | ""
    problem: str = ""      # plain words when url is None


def serve_candidates(doc, gateway_port) -> list[str]:
    """https addresses in a `tailscale serve status --json` document whose "/" proxies to the gateway."""
    out = []
    for hostport, web in ((doc or {}).get("Web") or {}).items():
        handler = ((web or {}).get("Handlers") or {}).get("/") or {}
        m = _LOOPBACK_PROXY.match(str(handler.get("Proxy") or ""))
        if m and int(m.group(2)) == int(gateway_port):
            host, _, port = str(hostport).rpartition(":")
            url = f"https://{host}" if port == "443" else f"https://{hostport}"
            if url not in out:
                out.append(url)
    return out


def detect(gateway_port, run=subprocess.run, timeout=SERVE_TIMEOUT_S) -> list[str]:
    """Ask tailscale, briefly. No tailscale, not logged in, a slow daemon or unreadable output all
    mean "nothing found", never an error."""
    try:
        r = run(["tailscale", "serve", "status", "--json"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(r, "returncode", 1) != 0:
        return []
    try:
        return serve_candidates(json.loads(r.stdout or "{}"), gateway_port)
    except (ValueError, TypeError, AttributeError):
        return []


def how_to_fix(gateway_port) -> str:
    return (f"Run the tailscale serve step from docs/ONBOARDING.md (step 4) on the server, for example "
            f"`tailscale serve --bg --https=8445 http://127.0.0.1:{gateway_port}`, then try again. Or set "
            f"public_url under [gateway] in orchestra.toml to the https address your devices use.")


def resolve(explicit=None, env=None, config_value="", gateway_port=8890, run=None) -> Resolution:
    from scripts.pairing import valid_base_url
    env = os.environ if env is None else env
    for source, value in (("explicit", explicit), ("env", env.get("ORCHESTRA_PUBLIC_URL")),
                          ("config", config_value)):
        value = str(value or "").strip()
        if value:
            if valid_base_url(value):
                return Resolution(value, source)
            return Resolution(None, source, f"{value!r} is not an https address a device can reach "
                                            f"(it needs https:// and a host).")
    found = [u for u in (detect(gateway_port, run=run) if run else detect(gateway_port)) if valid_base_url(u)]
    if len(found) == 1:
        return Resolution(found[0], "tailscale")
    if len(found) > 1:
        return Resolution(None, "tailscale", "More than one https address serves this gateway ("
                          + ", ".join(found) + "). Set public_url under [gateway] in orchestra.toml to "
                          "the one your devices use.")
    return Resolution(None, "", "I do not know the https address your devices use to reach this "
                                "server. " + how_to_fix(gateway_port))
