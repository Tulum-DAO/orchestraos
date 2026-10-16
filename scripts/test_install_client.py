"""scripts/install-client.sh: the guided client setup (docs/CLIENT_SETUP.md).

Every command the script runs is a stub on PATH that records its call and reads its answers from
state files, so a test can play a whole client: a fresh server, each person-only stop, the re-run
that continues, and a final re-run that must change nothing.
"""
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install-client.sh"

# name -> bash body. $S is the state dir; every stub logs "<name> <args>" to $S/calls.
STUBS = {
    "sudo": 'if [ "$1" = "-E" ]; then shift; fi; exec "$@"',
    "dpkg": '[ -f "$S/pkgs" ] && exit 0 || exit 1',
    "apt-get": 'if [ "$1" = "update" ] && [ -f "$S/apt_update_fails" ]; then n=$(cat "$S/apt_update_fails"); '
               '[ "$n" -gt 0 ] && { echo $((n-1)) > "$S/apt_update_fails"; exit 1; }; fi; '
               'case "$*" in *nodejs*) touch "$S/node";; *install*) touch "$S/pkgs";; esac; exit 0',
    "node": '[ -f "$S/node" ] && echo v22.23.3 || exit 127',
    "curl": 'echo "echo setup-script-ran"',
    "tailscale": 'case "$1" in '
                 'status) if [ "$2" = "--json" ]; then [ -f "$S/ts_running" ] && echo \'{"BackendState":"Running","Self":{"DNSName":"box.example.ts.net."}}\' '
                 '|| echo \'{"BackendState":"NeedsLogin"}\'; fi;; '
                 'up) [ -f "$S/ts_signin" ] && touch "$S/ts_running" || { echo "To authenticate, visit: https://login.example/x"; exit 1; };; '
                 'serve) if [ "$2" = "status" ]; then [ -f "$S/serve_json" ] && { cat "$S/serve_json"; exit 0; }; '
                 '[ -f "$S/serve_broken" ] && exit 1; python3 -c "import json,sys; w={}; '
                 '[w.__setitem__(\'box.example.ts.net:\'+l.split()[0], {\'Handlers\': {\'/\': {\'Proxy\': l.split()[1]}}}) for l in open(sys.argv[1])]; '
                 'print(json.dumps({\'Web\': w}))" "$S/ts_serve" 2>/dev/null || echo "{}"; '
                 'else p="${3#--https=}"; echo "$p ${*: -1}" >> "$S/ts_serve"; fi;; esac; exit 0',
    "npm": 'case "$*" in *claude-code@*) echo "${*##*@}" > "$S/claude_ver";; *codex@*) echo "${*##*@}" > "$S/codex_ver";; esac',
    "claude": 'case "$1" in --version) [ -f "$S/claude_ver" ] && echo "$(cat "$S/claude_ver") (Claude Code)" || exit 127;; '
              'auth) [ -f "$S/claude_login" ] && echo \'{"loggedIn": true}\' || echo \'{"loggedIn": false}\';; esac',
    "codex": '[ "$1" = "--version" ] && { [ -f "$S/codex_ver" ] && echo "codex-cli $(cat "$S/codex_ver")" || exit 127; }',
    "git": 'if [ "$1" = "clone" ]; then mkdir -p "$3/.git"; fi',
    "make": 'mkdir -p "$HOME/.local/bin"; cp "$S/orchestra_impl" "$HOME/.local/bin/orchestra"; chmod +x "$HOME/.local/bin/orchestra"',
    "systemctl": '[ "$1" = "is-active" ] && { [ -f "$S/unit" ] && echo active || echo inactive; }',
    "tmux": 'case "$1" in has-session) [ -f "$S/gm" ] && exit 0 || exit 1;; kill-session) rm -f "$S/gm";; esac',
    "sleep": 'exit 0',
    "id": '[ -f "$S/user_exists" ] && exit 0 || exit 1',
    "adduser": 'touch "$S/user_exists"',
    "usermod": 'exit 0',
    "install": 'exit 0',
    "su": 'exit 0',
}
# `orchestra` arrives via `make install` (as on a real box), so it is not on PATH before step 8.
VENV_PYTHON = r'''#!/usr/bin/env bash
echo "venv-python $*" >> "$S/calls"
[ "$1" = "-c" ] && { [ -f "$S/segno" ] && exit 0 || exit 1; }
[ "$2" = "pip" ] && { touch "$S/segno"; exit 0; }
case "$1 $2" in
  *approval.py\ request) echo "apr_welcome_0001"; exit 0;;
  *approval.py\ get) [ -f "$S/welcome_answered" ] && echo '{"status": "resolved"}' || echo '{"status": "pending"}'; exit 0;;
esac
exit 0
'''
ORCHESTRA = r'''#!/usr/bin/env bash
echo "orchestra $*" >> "$S/calls"
case "$1" in
  init) printf '[runtimes]\nenabled = ["gemini"]\n' > orchestra.toml; mkdir -p api/dist .venv/bin; touch api/dist/server.js
        cp "$S/venv_python_impl" .venv/bin/python
        chmod +x .venv/bin/python;;
  install-unit) touch "$S/unit";;
  spawn) touch "$S/gm"; [ -f "$S/spawn_fails" ] && { rm -f "$S/spawn_fails"; exit 1; };;
  devices) [ "$2" = "--revoke" ] && { rm -f "$S/minted"; echo "Revoked $3."; exit 0; }
           [ -f "$S/minted" ] && echo "dev_unused         my-phone               read,approve                       never                active"
           [ -f "$S/paired" ] && echo "dev_abc123         my-phone               read,approve                       2026-10-16 15:00 UTC active"
           echo "Note: ...";;
  pair) [ -f "$S/pair_fails" ] && { echo "Run the tailscale serve step from docs/ONBOARDING.md"; exit 2; }
        touch "$S/minted"; echo "[QR CODE]";;
  doctor) [ "$2" = "--share" ] && echo "--- orchestra doctor --share ---" || echo "CHECK STATUS";;
esac
exit 0
'''


@pytest.fixture
def box(tmp_path):
    s, home, bin_ = tmp_path / "state", tmp_path / "home", tmp_path / "bin"
    for d in (s, home, bin_):
        d.mkdir()
    for name, body in STUBS.items():
        p = bin_ / name
        p.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$S/calls"\n{body}\n')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    (s / "orchestra_impl").write_text(ORCHESTRA)
    (s / "venv_python_impl").write_text(VENV_PYTHON)
    osr = tmp_path / "os-release"
    osr.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:        8000000 kB\n")
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(home), "S": str(s), "USER": "orchestra",
           "ORCH_INSTALL_ALLOW_NO_TTY": "1", "ORCH_INSTALL_EUID": "1000", "ORCH_INSTALL_OS_RELEASE": str(osr),
           "ORCH_INSTALL_MEMINFO": str(mem), "ORCH_INSTALL_RETRY_SLEEP": "0"}

    class Box:
        state, path = s, tmp_path
        def run(self, **extra):
            r = subprocess.run(["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True,
                               timeout=60, cwd=str(home))
            return r
        def calls(self):
            p = s / "calls"
            return p.read_text().splitlines() if p.exists() else []
        def set(self, *names):
            for n in names:
                (s / n).touch()
        def os_release(self, text):
            osr.write_text(text)
    return Box()


def test_a_client_walks_through_every_stop_and_ends_complete(box):
    r = box.run()
    assert r.returncode == 3, r.stdout + r.stderr                 # stops at the Tailscale sign-in
    assert "Step 4 of 10 done: Tailscale installed" in r.stdout
    assert "Step 5 of 10 needs you" in r.stdout and "run the same line again" in r.stdout
    box.set("ts_signin")
    r = box.run()
    assert r.returncode == 3, r.stdout                            # now stops at the agent-tool sign-in
    assert "Continuing at step 5" in r.stdout
    assert "Step 7 of 10 needs you" in r.stdout and "Type:  claude" in r.stdout
    box.set("claude_login")
    r = box.run()
    assert r.returncode == 3, r.stdout                            # now stops at the welcome card
    assert "Step 9 of 10 done" in r.stdout and "Step 10 of 10 needs you" in r.stdout
    assert "https://box.example.ts.net/" in r.stdout and "Approve" in r.stdout
    assert "[QR CODE]" not in r.stdout                            # no app for clients yet: no pairing by default
    box.set("welcome_answered")
    r = box.run()
    assert r.returncode == 0, r.stdout
    assert "Setup complete" in r.stdout and "--- orchestra doctor --share ---" in r.stdout
    served = (box.state / "ts_serve").read_text()
    assert "127.0.0.1:8891" in served and "127.0.0.1:8890" in served      # dashboard + the phone's gateway
    assert sum("approval.py request" in c for c in box.calls()) == 1      # one welcome card, filed once
    toml = (box.path / "home" / "orchestraos" / "orchestra.toml").read_text()
    assert 'enabled = ["claude"]' in toml


def test_a_re_run_after_setup_changes_nothing(box):
    box.set("ts_signin", "claude_login", "welcome_answered")
    assert box.run().returncode == 0
    (box.state / "calls").unlink()
    r = box.run()
    assert r.returncode == 0, r.stdout
    acted = [c for c in box.calls() if re.match(r"(apt-get (update|install)|npm install|git clone|make |orchestra (init|install-unit|spawn|pair)|tailscale (up|serve --bg)|adduser)", c)]
    assert acted == [], acted
    assert "Continuing at step" not in r.stdout


def test_piped_into_bash_it_refuses_and_says_how_to_run_it(box):
    r = box.run(ORCH_INSTALL_ALLOW_NO_TTY="")
    assert r.returncode == 2
    assert "-o install-client.sh && bash install-client.sh" in r.stdout
    assert box.calls() == []


def test_the_wrong_system_stops_at_step_one_with_the_fix(box):
    box.os_release('ID=debian\nVERSION_ID="12"\n')
    r = box.run()
    assert r.returncode == 4
    assert "Step 1 of 10 did not finish" in r.stdout and "pick Ubuntu 24.04 LTS" in r.stdout
    assert box.calls() == []


def test_as_root_it_creates_the_user_and_continues_as_that_user(box):
    r = box.run(ORCH_INSTALL_EUID="0")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = box.calls()
    assert any(c.startswith("adduser --gecos  orchestra") for c in calls), calls
    assert any(c.startswith("usermod -aG sudo orchestra") for c in calls)
    assert any(c.startswith("su - orchestra -c bash ~/install-client.sh") for c in calls)
    assert not any(c.startswith(("apt-get", "npm", "git")) for c in calls)   # nothing else as root


def test_a_flaky_network_is_retried_before_it_fails(box):
    (box.state / "apt_update_fails").write_text("2")
    r = box.run()
    assert "Step 2 of 10 done" in r.stdout, r.stdout
    (box.state / "apt_update_fails").write_text("5")
    (box.state / "pkgs").unlink(missing_ok=True)
    r = box.run()
    assert r.returncode == 4 and "Step 2 of 10 did not finish" in r.stdout


def test_codex_clients_get_the_pinned_codex_and_its_own_sign_in_check(box):
    box.set("ts_signin")
    r = box.run(ORCH_INSTALL_RUNTIME="codex")
    assert r.returncode == 3 and "Type:  codex login" in r.stdout, r.stdout
    assert any("@openai/codex@" in c for c in box.calls())
    auth = box.path / "home" / ".codex"
    auth.mkdir()
    (auth / "auth.json").write_text(json.dumps({"tokens": {"x": 1}}))
    r = box.run(ORCH_INSTALL_RUNTIME="codex")
    assert "Step 7 of 10 done: signed in to codex" in r.stdout, r.stdout


def test_the_pins_match_the_install_guide_and_doctor():
    src = SCRIPT.read_text()
    claude = re.search(r'^CLAUDE_PIN="([^"]+)"', src, re.M).group(1)
    codex = re.search(r'^CODEX_PIN="([^"]+)"', src, re.M).group(1)
    guide = (ROOT / "docs" / "INSTALL.md").read_text()
    assert f"sudo npm install -g @anthropic-ai/claude-code@{claude}" in guide
    import sys
    sys.path.insert(0, str(ROOT))
    from orchestra_cli.doctor import PROVEN_CLI_VERSIONS
    assert claude in PROVEN_CLI_VERSIONS["claude"] and codex in PROVEN_CLI_VERSIONS["codex"]


def test_a_minted_but_never_scanned_code_is_not_a_paired_phone(box):
    box.set("ts_signin", "claude_login", "welcome_answered")
    r = box.run(ORCH_INSTALL_PAIR_PHONE="1")
    assert r.returncode == 3 and "has not used its code yet" in r.stdout, r.stdout
    assert (box.state / "minted").exists()
    r = box.run(ORCH_INSTALL_PAIR_PHONE="1")                                                   # the unscanned device still blocks
    assert r.returncode == 3 and "Step 10 of 10 needs you" in r.stdout
    assert r.stdout.index("Pairing your phone app") < r.stdout.index("[QR CODE]")   # instructions before the code clears


def test_by_default_there_is_no_phone_pairing_the_dashboard_is_the_way_in(box):
    box.set("ts_signin", "claude_login", "welcome_answered")
    r = box.run()
    assert r.returncode == 0, r.stdout
    assert not any(c.startswith("orchestra pair") for c in box.calls())


def test_the_welcome_card_must_be_answered_before_setup_is_complete(box):
    box.set("ts_signin", "claude_login")
    assert box.run().returncode == 3
    r = box.run()                                                   # still not answered: same stop, no second card
    assert r.returncode == 3 and "Welcome" in r.stdout
    assert sum("approval.py request" in c for c in box.calls()) == 1
    box.set("welcome_answered")
    r = box.run()
    assert r.returncode == 0 and "Setup complete" in r.stdout, r.stdout


def test_the_qr_encoder_is_installed_so_the_phone_can_scan(box):
    box.set("ts_signin", "claude_login", "welcome_answered")
    assert box.run().returncode == 0
    assert any(c.startswith("venv-python -m pip install") and "segno" in c for c in box.calls())


def test_a_failed_first_spawn_is_cleared_and_retried_not_counted_as_running(box):
    box.set("ts_signin", "claude_login", "welcome_answered", "spawn_fails")
    r = box.run()
    assert r.returncode == 4 and "the first agent did not start" in r.stdout, r.stdout
    assert (box.state / "gm").exists()                              # the failed spawn left its pane
    r = box.run()
    assert r.returncode == 0, r.stdout
    calls = box.calls()
    assert "tmux kill-session -t gm" in calls
    assert sum(c.startswith("orchestra spawn gm --gm") for c in calls) == 2


def test_a_new_code_revokes_the_previous_unscanned_one_so_unused_devices_do_not_pile_up(box):
    box.set("ts_signin", "claude_login", "welcome_answered")
    assert box.run(ORCH_INSTALL_PAIR_PHONE="1").returncode == 3                                # first code minted, not scanned
    r = box.run(ORCH_INSTALL_PAIR_PHONE="1")                                                   # second try
    assert r.returncode == 3
    calls = box.calls()
    assert "orchestra devices --revoke dev_unused" in calls, calls
    assert sum(c.startswith("orchestra pair") for c in calls) == 2


def test_when_the_code_cannot_be_made_the_client_gets_the_reason_not_a_scan_hint(box):
    box.set("ts_signin", "claude_login", "welcome_answered", "pair_fails")
    r = box.run(ORCH_INSTALL_PAIR_PHONE="1")
    assert r.returncode == 4, r.stdout
    assert "the pairing code could not be made" in r.stdout
    assert "has not used its code" not in r.stdout


def test_a_port_already_serving_something_else_is_never_replaced(box):
    (box.state / "ts_serve").write_text("443 http://127.0.0.1:3000\n")      # the client's own app
    box.set("ts_signin", "claude_login")
    r = box.run()
    assert r.returncode == 4, r.stdout
    assert "already serves something else" in r.stdout and "443" in r.stdout
    assert not any(c.startswith("tailscale serve --bg") for c in box.calls())
    assert (box.state / "ts_serve").read_text() == "443 http://127.0.0.1:3000\n"


def _serve_json(box, doc):
    (box.state / "serve_json").write_text(json.dumps(doc))
    box.set("ts_signin", "claude_login")
    r = box.run()
    return r


def test_a_named_tailscale_service_on_the_port_is_seen_and_not_replaced(box):
    r = _serve_json(box, {"Services": {"svc:myapp": {"Web": {"svc-myapp.example.ts.net:443":
                    {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}}}})
    assert r.returncode == 4 and "already serves something else" in r.stdout, r.stdout
    assert not any(c.startswith("tailscale serve --bg") for c in box.calls())


def test_our_own_port_with_funnel_on_is_refused_as_public(box):
    r = _serve_json(box, {"Web": {"box.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8891"}}}},
                          "AllowFunnel": {"box.example.ts.net:443": True}})
    assert r.returncode == 4 and "public internet" in r.stdout, r.stdout


def test_when_serve_status_cannot_be_read_it_stops_instead_of_guessing(box):
    box.set("serve_broken", "ts_signin", "claude_login")
    r = box.run()
    assert r.returncode == 4 and "could not read" in r.stdout, r.stdout
    assert not any(c.startswith("tailscale serve --bg") for c in box.calls())


def test_the_welcome_card_is_a_plain_approval_the_dashboard_button_can_answer(box):
    # Found in the container run: --options "Approve" made a MENU row, which the dashboard's Approve
    # route refuses ("menu rows answer via answer=='option' only").
    box.set("ts_signin", "claude_login")
    box.run()
    req = [c for c in box.calls() if "approval.py request" in c]
    assert req and "--options approve,deny" in req[0], req
