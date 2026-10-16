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
        D.Check("runtime:claude", D.OK, detail="2.1.295 at /home/alice/.local/bin/claude"),
        D.Check("runtime:login", D.MISSING, detail="loggedIn false for alice", remedy="run claude, sign in"),
        D.Check("port:gateway", D.OK, detail="owned by pid 4242 (alice)"),
        D.Check("dashboard:https", D.WARN, detail="box-7.tail1234.ts.net 100.101.102.103", required=False),
        D.Check("seat:sk-ant-oat01-XYZ", D.OK),                     # a name that is not a plain check name
        D.Check("gateway token", D.OK, detail="abcd1234-ef56 in /home/alice/.orchestra/token"),
    ]


def test_the_block_carries_name_status_and_required_only():
    out = D.render_share(rows(), facts={"release": "152c0a6", "os": "Ubuntu 24.04", "ram_gb": 4, "disk_free_gb": 61})
    assert "runtime:claude OK" in out
    assert "runtime:login MISSING (required)" in out
    assert "dashboard:https WARN" in out and "port:gateway OK" in out
    assert "release 152c0a6" in out and "Ubuntu 24.04" in out and "ram 4 GB" in out


def test_nothing_identifying_ever_appears():
    out = D.render_share(rows(), facts={"release": "152c0a6", "os": "Ubuntu 24.04", "ram_gb": 4, "disk_free_gb": 61})
    for s in SECRETS:
        assert s not in out, s


def test_only_known_check_names_are_printed():
    out = D.render_share(rows(), facts={})
    assert "seat:sk-ant" not in out
    assert out.count("(other check)") == 2                        # the token-shaped name and the one with a space


def test_a_lowercase_token_shaped_name_never_prints():
    # codex review of #452: a character pattern alone let a lowercase token-shaped name through.
    for name in ("seat:sk-ant-oat01-abcdefgh", "ghp_abcdefghijklmnop", "tail1234", "alice", "box-7"):
        out = D.render_share([D.Check(name, D.OK)], facts={})
        assert name not in out, (name, out)


def test_a_family_name_keeps_its_family_but_an_unknown_suffix_is_hidden():
    out = D.render_share([D.Check("plugin:my-secret-bot", D.OK), D.Check("port:gateway", D.OK),
                          D.Check("runtime:claude", D.OK), D.Check("cli:codex-version", D.WARN)], facts={})
    assert "plugin:(other) OK" in out and "my-secret-bot" not in out
    assert "port:gateway OK" in out and "runtime:claude OK" in out and "cli:codex-version WARN" in out


def test_every_fixed_name_doctor_writes_is_shareable():
    import re
    src = open(D.__file__).read()
    names = set(re.findall(r'Check\("([^"]+)"', src))
    assert names, "no fixed names found"
    out = D.render_share([D.Check(n, D.OK) for n in sorted(names)], facts={})
    assert "(other" not in out, [l for l in out.splitlines() if "(other" in l]


def test_facts_are_allowlisted_too():
    out = D.render_share([], facts={"release": "152c0a6", "hostname": "box-7", "user": "alice", "ip": "100.101.102.103"})
    assert "box-7" not in out and "alice" not in out and "100.101" not in out
