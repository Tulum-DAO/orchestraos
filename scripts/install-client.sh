#!/usr/bin/env bash
# install-client.sh: set up OrchestraOS on a fresh Ubuntu server, one step at a time, in plain English.
#
# For someone who has never used a terminal. docs/CLIENT_SETUP.md is their page; this script does
# what docs/INSTALL.md's minimum path does, in the same order, so that guide stays the reference.
#
# Run it as one line from the server's console (it must be a file, not piped into bash, because
# the steps that ask for a password need the keyboard):
#   curl -fsSL https://raw.githubusercontent.com/Tulum-DAO/orchestraos/main/scripts/install-client.sh -o install-client.sh && bash install-client.sh
#
# Safe to run again at any time. Every step checks first and does nothing if it is already done,
# so a re-run picks up at the first unfinished step and says so. When a step needs the person
# (a password, a sign-in, scanning a code), it says why in one sentence, shows exactly what to do,
# and stops; running the same line again continues. Nothing here sends anything anywhere.
#
# Tests stub every command through PATH (scripts/test_install_client.py). ORCH_INSTALL_* variables
# exist for those tests, except two a person may use: ORCH_INSTALL_RUNTIME=codex (sign in with a
# ChatGPT plan instead of Claude) and ORCH_INSTALL_PAIR_PHONE=1 (pair the phone app, once you have it).
set -uo pipefail

CLAUDE_PIN="2.1.295"            # = docs/INSTALL.md "Pin the agent CLI version" (a test keeps them equal)
CODEX_PIN="0.153.4"             # = orchestra_cli/doctor.py PROVEN_CLI_VERSIONS["codex"]
RUNTIME="${ORCH_INSTALL_RUNTIME:-claude}"     # claude (default) or codex
REPO_URL="${ORCH_INSTALL_REPO_URL:-https://github.com/Tulum-DAO/orchestraos.git}"
CHECKOUT="${ORCH_INSTALL_CHECKOUT:-$HOME/orchestraos}"
OS_RELEASE="${ORCH_INSTALL_OS_RELEASE:-/etc/os-release}"
MEMINFO="${ORCH_INSTALL_MEMINFO:-/proc/meminfo}"
EUID_NOW="${ORCH_INSTALL_EUID:-$EUID}"
PACKAGES=(git tmux python3 python3-venv build-essential curl iproute2)
TOTAL=10

say()   { printf '%s\n' "$*"; }
done_() { say "Step $1 of $TOTAL done: $2"; }
# A person-only stop: why in one sentence, exactly what to do, then how to continue. Exit 3.
stop_for_person() {
  local step="$1" why="$2"; shift 2
  say ""
  say "Step $step of $TOTAL needs you: $why"
  for line in "$@"; do say "    $line"; done
  say "When that is done, run the same line again to continue:"
  say "    bash install-client.sh"
  exit 3
}
# A failure: what went wrong, the one thing to do, then retry. Exit 4.
fail() {
  local step="$1" what="$2" fix="$3"
  say ""
  say "Step $step of $TOTAL did not finish: $what"
  say "    What to do: $fix"
  say "Then run the same line again: bash install-client.sh"
  exit 4
}
# Network steps get three tries before they fail.
retry() { local n; for n in 1 2 3; do "$@" && return 0; [ "$n" -lt 3 ] && sleep "${ORCH_INSTALL_RETRY_SLEEP:-5}"; done; return 1; }

if [ ! -t 0 ] && [ -z "${ORCH_INSTALL_ALLOW_NO_TTY:-}" ]; then
  say "This setup asks for a password and a sign-in, so it needs your keyboard."
  say "Run it as a file instead of piping it into bash:"
  say "    curl -fsSL https://raw.githubusercontent.com/Tulum-DAO/orchestraos/main/scripts/install-client.sh -o install-client.sh && bash install-client.sh"
  exit 2
fi
case "$RUNTIME" in claude|codex) ;; *) say "ORCH_INSTALL_RUNTIME must be claude or codex, not '$RUNTIME'."; exit 2 ;; esac

# ---- As root: make the normal user the agents run as, then continue as that user. ----------------
# The agents refuse to run as root (claude --dangerously-skip-permissions refuses root), so this
# is the only thing done as root. Its password is the one `sudo` asks for later.
if [ "$EUID_NOW" = "0" ]; then
  user="${ORCH_INSTALL_USER:-orchestra}"
  if ! id "$user" >/dev/null 2>&1; then
    say "You are logged in as root. OrchestraOS runs as a normal user, so this creates one named '$user'."
    say "Choose a password for it when asked (nothing shows while you type), and keep it: you will need it."
    adduser --gecos "" "$user" || fail 1 "the user '$user' was not created" "answer the password questions, then try again"
  fi
  usermod -aG sudo "$user" || fail 1 "'$user' could not be given sudo" "run: usermod -aG sudo $user"
  if [ -f /root/.ssh/authorized_keys ]; then
    install -d -m 700 -o "$user" -g "$user" "/home/$user/.ssh"
    install -m 600 -o "$user" -g "$user" /root/.ssh/authorized_keys "/home/$user/.ssh/authorized_keys"
  fi
  install -m 755 -o "$user" -g "$user" "$0" "/home/$user/install-client.sh"
  say "Continuing as '$user'. From now on, log in as '$user' and run: bash install-client.sh"
  exec su - "$user" -c "bash ~/install-client.sh"
fi

started=""
resume() { [ -z "$started" ] && { started=1; [ "$1" -gt 2 ] && say "Already done: steps 1 to $(( $1 - 1 )). Continuing at step $1."; }; true; }

# ---- 1. The server ------------------------------------------------------------------------------
os_id=$(. "$OS_RELEASE" 2>/dev/null; echo "${ID:-}"); os_ver=$(. "$OS_RELEASE" 2>/dev/null; echo "${VERSION_ID:-}")
if [ "$os_id" != "ubuntu" ] || { [ "$os_ver" != "24.04" ] && [ "$os_ver" != "22.04" ]; }; then
  fail 1 "this server runs ${os_id:-an unknown system} ${os_ver}, and this setup needs Ubuntu 24.04 (or 22.04)" \
       "create the server again and pick Ubuntu 24.04 LTS"
fi
mem_kb=$(awk '/^MemTotal:/{print $2}' "$MEMINFO" 2>/dev/null || echo 0)
if [ "${mem_kb:-0}" -lt 3500000 ]; then
  say "Note: this server has less than 4 GB of memory. It will start, but plan on 4 GB once agents run."
fi
done_ 1 "this server is Ubuntu $os_ver"

# ---- 2. System packages --------------------------------------------------------------------------
missing=(); for p in "${PACKAGES[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
  resume 2
  say "Installing the tools OrchestraOS needs (${missing[*]}). sudo asks for your password the first time."
  retry sudo apt-get update || fail 2 "the package list could not be downloaded" "check the server has internet, wait a minute"
  retry sudo apt-get install -y "${PACKAGES[@]}" || fail 2 "the tools could not be installed" "wait a minute (another update may be running)"
fi
done_ 2 "system tools installed"

# ---- 3. Node.js 22 -------------------------------------------------------------------------------
if ! node -v 2>/dev/null | grep -q '^v22\.'; then
  resume 3
  say "Installing Node.js 22 (it runs the dashboard and the agent tool)."
  retry bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -' \
    || fail 3 "the Node.js source could not be added" "check the server has internet, wait a minute"
  retry sudo apt-get install -y nodejs || fail 3 "Node.js could not be installed" "wait a minute"
  node -v 2>/dev/null | grep -q '^v22\.' || fail 3 "Node.js 22 is not the version this server runs" "run: node -v, and send what it prints to whoever helps you"
fi
done_ 3 "Node.js $(node -v 2>/dev/null) installed"

# ---- 4. Tailscale (your private network) ---------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  resume 4
  say "Installing Tailscale, which puts this server on a private network only your devices can join."
  retry bash -c 'curl -fsSL https://tailscale.com/install.sh | sh' || fail 4 "Tailscale could not be installed" "check the server has internet, wait a minute"
fi
done_ 4 "Tailscale installed"

# ---- 5. Join your private network [person] -------------------------------------------------------
ts_state() { tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null; }
if [ "$(ts_state)" != "Running" ]; then
  resume 5
  say ""
  say "Step 5 of $TOTAL needs you: sign this server in to your private network."
  say "    A link appears below. Open it on your phone or computer and sign in (Google, Microsoft, Apple or GitHub)."
  say "    Then install the Tailscale app on your phone and sign in with the SAME account."
  sudo tailscale up || stop_for_person 5 "the sign-in did not finish." "Open the link it printed and sign in."
  [ "$(ts_state)" = "Running" ] || stop_for_person 5 "the server is not on your private network yet." "Open the link it printed and sign in."
fi
sudo tailscale set --operator="$USER" >/dev/null 2>&1 || true
done_ 5 "this server is on your private network"

# ---- 6. The agent tool, pinned ---------------------------------------------------------------------
if [ "$RUNTIME" = "claude" ]; then pkg="@anthropic-ai/claude-code@$CLAUDE_PIN"; bin=claude; pin="$CLAUDE_PIN"
else pkg="@openai/codex@$CODEX_PIN"; bin=codex; pin="$CODEX_PIN"; fi
if ! "$bin" --version 2>/dev/null | grep -q "$pin"; then
  resume 6
  say "Installing $bin $pin (the version OrchestraOS is tested with)."
  retry sudo npm install -g "$pkg" || fail 6 "$bin could not be installed" "check the server has internet, wait a minute"
fi
grep -q 'DISABLE_AUTOUPDATER=1' "$HOME/.bashrc" 2>/dev/null || echo 'export DISABLE_AUTOUPDATER=1' >> "$HOME/.bashrc"
export DISABLE_AUTOUPDATER=1
done_ 6 "$bin $pin installed"

# ---- 7. Sign in to the agent tool [person] -------------------------------------------------------
logged_in() {
  if [ "$RUNTIME" = "claude" ]; then claude auth status 2>/dev/null | grep -q '"loggedIn": *true'
  else python3 -c 'import json,os,sys; d=json.load(open(os.path.expanduser("~/.codex/auth.json"))); sys.exit(0 if d.get("tokens") else 1)' 2>/dev/null; fi
}
if ! logged_in; then
  resume 7
  if [ "$RUNTIME" = "claude" ]; then
    stop_for_person 7 "your agents use your own Claude plan, so you sign in to it once (your password goes to Anthropic, never to this script)." \
      "Type:  claude" "Follow the link it shows and sign in, then type  /exit"
  else
    stop_for_person 7 "your agents use your own ChatGPT plan, so you sign in to it once (your password goes to OpenAI, never to this script)." \
      "Type:  codex login" "Follow the link it shows and sign in."
  fi
fi
done_ 7 "signed in to $bin"

# ---- 8. OrchestraOS --------------------------------------------------------------------------------
if [ ! -d "$CHECKOUT/.git" ]; then
  resume 8
  say "Downloading OrchestraOS."
  retry git clone "$REPO_URL" "$CHECKOUT" || fail 8 "OrchestraOS could not be downloaded" "check the server has internet, wait a minute"
fi
export PATH="$HOME/.local/bin:$PATH"
if [ ! -f "$CHECKOUT/orchestra.toml" ] || [ ! -f "$CHECKOUT/api/dist/server.js" ] || ! command -v orchestra >/dev/null 2>&1; then
  resume 8
  say "Setting up OrchestraOS (a few minutes: it installs and builds its parts)."
  ( cd "$CHECKOUT" && make install ) || fail 8 "the orchestra command could not be installed" "run the same line again; if it fails twice, send the last lines to whoever helps you"
  ( cd "$CHECKOUT" && orchestra init --yes ) || fail 8 "the setup did not finish" "run the same line again; it continues where it stopped"
fi
# The QR code for the phone (`orchestra pair` draws it with segno when the checkout's Python has it;
# without it the phone has to take a long typed code).
if ! "$CHECKOUT/.venv/bin/python" -c 'import segno' >/dev/null 2>&1; then
  retry "$CHECKOUT/.venv/bin/python" -m pip install -q segno || say "Note: the QR code could not be installed; step 10 shows a code to paste instead."
fi
if ! grep -qE "^enabled = \[\"$RUNTIME\"\]" "$CHECKOUT/orchestra.toml" 2>/dev/null; then
  sed -i "/^\[runtimes\]/,/^\[/ s/^enabled = .*/enabled = [\"$RUNTIME\"]/" "$CHECKOUT/orchestra.toml"
fi
done_ 8 "OrchestraOS installed"

# ---- 9. Keep it running, also after a reboot -----------------------------------------------------
if [ "$(systemctl is-active orchestra 2>/dev/null)" != "active" ]; then
  resume 9
  say "Starting OrchestraOS and setting it to start again after a reboot."
  ( cd "$CHECKOUT" && orchestra install-unit --now ) || fail 9 "OrchestraOS did not start" "run: orchestra doctor, and fix the rows it marks MISSING"
fi
# Put the dashboard (443) and the phone's gateway (8445, docs/ONBOARDING.md step 4) on your private
# network: tailnet only, never Funnel. A port that already serves something else is never replaced.
served_on() {   # what https port $1 serves on this tailnet: nothing, PROXY <target>, or FUNNEL <target>; exit 2 = unreadable
  local js
  js=$(tailscale serve status --json 2>/dev/null) || return 2
  printf '%s' "$js" | python3 -c '
import json, sys
port = sys.argv[1]
try:
    doc = json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(2)
funnel = doc.get("AllowFunnel") or {}
# Top-level entries, and those of named Tailscale Services (Services.<name>.Web).
webs = [doc.get("Web") or {}] + [(svc or {}).get("Web") or {} for svc in (doc.get("Services") or {}).values()]
for web in webs:
    for hostport, w in web.items():
        if hostport.rsplit(":", 1)[-1] == port:
            target = (((w or {}).get("Handlers") or {}).get("/") or {}).get("Proxy") or "something-else"
            print(("FUNNEL " if funnel.get(hostport) else "PROXY ") + target)
            sys.exit(0)' "$1"
}
serve_port() {
  local port="$1" target="$2" what="$3" cur rc
  cur=$(served_on "$port"); rc=$?
  if [ "$rc" -ne 0 ]; then
    fail 9 "this setup could not read what your private network already serves, so it will not change it" \
         "run  tailscale serve status  to check Tailscale is running, then try again"
  fi
  case "$cur" in
    "PROXY $target") return 0 ;;
    "FUNNEL "*) fail 9 "https port $port is open to the public internet (Tailscale Funnel); OrchestraOS must stay on your private network" \
                     "turn Funnel off for it with  tailscale funnel --https=$port off , then try again" ;;
    "") ;;
    *) fail 9 "https port $port on your private network already serves something else, which the $what was going to use; this setup will not replace it" \
            "if you set that up yourself, keep it and send whoever helps you the output of  tailscale serve status" ;;
  esac
  tailscale serve --bg --https="$port" "$target" >/dev/null 2>&1 \
    || fail 9 "the $what could not be put on your private network" "open the Tailscale admin page, turn on HTTPS certificates, then try again"
}
serve_port 443 http://127.0.0.1:8891 dashboard
serve_port 8445 http://127.0.0.1:8890 "phone app's address"
done_ 9 "OrchestraOS is running and starts again after a reboot"

# ---- 10. Your first agent and your phone [person] ------------------------------------------------
# Started = `orchestra spawn` succeeded (the marker) AND its session is still there. A spawn that
# fails leaves its pane running for inspection, so a session alone does not prove the agent got its
# instructions; the next run clears that pane and starts it again.
FIRST_AGENT_MARK="$HOME/.orchestra/.install-client-first-agent"
if [ ! -f "$FIRST_AGENT_MARK" ] || ! tmux has-session -t gm 2>/dev/null; then
  resume 10
  if tmux has-session -t gm 2>/dev/null; then
    say "Clearing the first agent's earlier attempt, which did not finish."
    tmux kill-session -t gm 2>/dev/null || true
  fi
  say "Starting your first agent, the General Manager."
  ( cd "$CHECKOUT" && orchestra spawn gm --gm ) || fail 10 "the first agent did not start" "run: orchestra doctor, and check the sign-in row (step 7)"
  mkdir -p "$(dirname "$FIRST_AGENT_MARK")" && date -u +%FT%TZ > "$FIRST_AGENT_MARK"
fi
dash="https://$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null)/"

# The end-to-end proof: one card the client answers on the dashboard. It goes through everything at
# once (Tailscale, the dashboard, the API, the card store), and the client sees it work themselves.
# Filed once; its id is kept so a re-run checks the same card.
WELCOME_ID_FILE="$HOME/.orchestra/.install-client-welcome-card"
approval() { ( cd "$CHECKOUT" && "$CHECKOUT/.venv/bin/python" scripts/approval.py "$@" ); }
welcome_answered() {
  [ -s "$WELCOME_ID_FILE" ] || return 1
  approval get "$(cat "$WELCOME_ID_FILE")" 2>/dev/null | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") not in (None, "pending") else 1)' 2>/dev/null
}
if ! welcome_answered; then
  resume 10
  if [ ! -s "$WELCOME_ID_FILE" ]; then
    id=$(approval request --from setup --worker-kind node --options "approve,deny" --risk low --reversibility easy \
           --feature "setup" --summary "Your OrchestraOS server is set up. Tap Approve to finish the setup on the server." \
           "Welcome to OrchestraOS! Tap Approve to finish setup." 2>/dev/null | tail -1)
    case "$id" in apr_*) mkdir -p "$(dirname "$WELCOME_ID_FILE")"; printf '%s\n' "$id" > "$WELCOME_ID_FILE" ;;
      *) fail 10 "the welcome card could not be created" "run: orchestra doctor, and fix the rows it marks MISSING" ;; esac
  fi
  welcome_answered || stop_for_person 10 "one last check that you can reach your agents from your own device." \
    "On your phone or computer (with Tailscale on), open:  $dash" \
    "Go to Approvals, find \"Welcome to OrchestraOS!\" and tap Approve."
fi

# Phone app pairing: opt in with ORCH_INSTALL_PAIR_PHONE=1 once you have the app (it is not in the
# App Store yet). `orchestra pair` makes the device record before any phone scans it; a phone that
# used its code has been SEEN, so "paired" = an active device whose LAST SEEN is not "never".
paired() { ( cd "$CHECKOUT" && orchestra devices 2>/dev/null ) | grep -E ' active$' | grep -vqE ' never +active$'; }
if [ -n "${ORCH_INSTALL_PAIR_PHONE:-}" ] && ! paired; then
  say ""
  say "Pairing your phone app: a code appears below for one minute. In the OrchestraOS app, choose Pair and scan it."
  # An earlier code that was never scanned left an active device record behind. Revoke those first,
  # so re-runs do not pile up unused devices.
  ( cd "$CHECKOUT" && orchestra devices 2>/dev/null ) | awk '$2 == "my-phone" && / never +active$/ {print $1}' |
    while read -r stale; do ( cd "$CHECKOUT" && orchestra devices --revoke "$stale" >/dev/null 2>&1 ) || true; done
  # read + approve: the phone sees your agents and answers their cards (docs/ONBOARDING.md step 2).
  ( cd "$CHECKOUT" && orchestra pair --scopes read,approve --label my-phone ) \
    || fail 10 "the pairing code could not be made (the lines above say why)" \
            "do what those lines say, or send the output of  orchestra doctor --share  to whoever helps you"
  paired || stop_for_person 10 "the phone has not used its code yet." \
    "If the app says it is connected, run the line again to finish." \
    "If the minute ran out, run the line again for a new code."
fi
done_ 10 "your first agent is running, and you reached it from your own device"


say ""
say "Setup complete. Your agents run on this server, and keep working when your laptop is closed."
( cd "$CHECKOUT" && orchestra doctor ) || true
say ""
say "If someone is helping you, send them this block (it holds no passwords, addresses or names):"
( cd "$CHECKOUT" && orchestra doctor --share ) || true
exit 0
