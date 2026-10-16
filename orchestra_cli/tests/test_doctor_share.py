"""`orchestra doctor --share`: a block a client can paste to whoever helps them, so their install
can be checked without anyone logging in to their server.

It is built by ALLOWLIST, never by redacting: each row contributes its name, status and whether it
is required, nothing else. Details and remedies can name paths, users, hosts or ports, so they never
appear, and a row added later cannot leak through a field nobody thought to redact.
"""
from orchestra_cli import doctor as D

SECRETS = ("/home/alice", "alice", "100.101.102.103", "box-7.tail1234.ts.net", "sk-ant-oat01-XYZ", "abcd1234-ef56")


def rows():
    return [
        D.Check("cli:claude", D.OK, detail="2.1.295 at /home/alice/.local/bin/claude"),
        D.Check("auth:claude", D.MISSING, detail="loggedIn false for alice", remedy="run claude, sign in"),
        D.Check("port:8891", D.OK, detail="owned by pid 4242 (alice)"),
        D.Check("tailscale", D.WARN, detail="box-7.tail1234.ts.net 100.101.102.103", required=False),
        D.Check("seat:sk-ant-oat01-XYZ", D.OK),                     # a name that is not a plain check name
        D.Check("gateway token", D.OK, detail="abcd1234-ef56 in /home/alice/.orchestra/token"),
    ]


def test_the_block_carries_name_status_and_required_only():
    out = D.render_share(rows(), facts={"release": "152c0a6", "os": "Ubuntu 24.04", "ram_gb": 4, "disk_free_gb": 61})
    assert "cli:claude OK" in out
    assert "auth:claude MISSING (required)" in out
    assert "tailscale WARN" in out
    assert "release 152c0a6" in out and "Ubuntu 24.04" in out and "ram 4 GB" in out


def test_nothing_identifying_ever_appears():
    out = D.render_share(rows(), facts={"release": "152c0a6", "os": "Ubuntu 24.04", "ram_gb": 4, "disk_free_gb": 61})
    for s in SECRETS:
        assert s not in out, s


def test_a_name_outside_the_plain_pattern_is_replaced_not_printed():
    out = D.render_share(rows(), facts={})
    assert "seat:sk-ant" not in out
    assert out.count("(unnamed check)") == 2                      # the token-shaped name and the one with a space


def test_facts_are_allowlisted_too():
    out = D.render_share([], facts={"release": "152c0a6", "hostname": "box-7", "user": "alice", "ip": "100.101.102.103"})
    assert "box-7" not in out and "alice" not in out and "100.101" not in out
