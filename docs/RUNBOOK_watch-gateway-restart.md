# Runbook: restarting the watch gateway

The watch gateway (`scripts/watch_gateway.py`, port 9091 by default) serves the phone, the watch, the
Mac app and the Quest, directly on the tailnet and through the login proxy. A restart is visible to the
operator, so it is done deliberately, once, in a quiet minute, and checked by effect afterwards.

## 1. When it needs a restart

The gateway imports `scripts/agent-status.py` and the approval modules **at start**. A change to one of
those files is not live until the gateway process is replaced. Most other changes (a kill file under
`state/`, a config value the module reads per call) take effect without a restart: check the module
before you reach for this runbook.

## 2. Only in a quiet minute

Nobody may be using a client when the gateway goes down. Zero device requests in the last 60 seconds
and no inject in flight:

```bash
GW=<data dir>/logs/gateway.log
since=$(date -d '-60 sec' '+%Y-%m-%d %H:%M:%S')
grep -a "^$(date '+%Y-%m-%d')" "$GW" | awk -v s="$since" 'substr($0,1,19) >= s' \
  | grep -a -cE 'Quest|OrchestraUltra|Dalvik|OculusBrowser|CFNetwork'      # 0 = go
ls <data dir>/state/gateway-inject-journal/ | wc -l                          # 0 = no inject in flight
```

A lone background client in a loop (a phone build polling a dead session every few seconds) never goes
quiet: look at which clients are in the lines; a loop is not a person using the app. Dashboard traffic
(`"node"` lines) does not count: the API polls the gateway and recovers on its own.

## 3. The act: SIGTERM the gateway, the supervisor relaunches it

Under `orchestra up` the gateway is a supervised child (`kind=service`): the supervisor reads its env file
and relaunches it the moment it exits. So the whole restart is one signal:

```bash
# The gateway of THIS install: its environment names your data dir. Never a bare `pgrep | head -1`: on a host
# with more than one install (a staging copy, a test box) that can signal another install's gateway.
gw_pid() { for p in $(pgrep -f 'scripts/watch_gateway.py'); do
             tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -qx "ORCHESTRA_DIR=$ORCHESTRA_DIR" && echo "$p"; done; }
pid=$(gw_pid); [ -n "$pid" ] && [ "$(echo "$pid" | wc -l)" = 1 ] || { echo "expected exactly one gateway, got: $pid"; exit 1; }
kill -TERM "$pid"
```

`ORCHESTRA_DIR` is the data dir the supervisor started this install with (`orchestra up` exports it to every
child; on a systemd install it is in the unit's environment).

- **SIGTERM, nothing else.** aiohttp stops accepting, finishes the handlers in flight (an inject completes
  its Enter and its verify) and exits; measured 12 s of drain on a busy gateway. Never `kill -9`, never
  `tmux respawn-pane -k`, never `C-c` into its pane: those cut a handler between "typed" and "Enter" and
  leave text unsent in a seat's composer.
- **Never start it by hand next to the supervisor.** If no new pid appears within 30 seconds, stop and
  report: a second gateway beside a supervised one fights for the port and the inject journal.
- A systemd install (`orchestra install-unit`) is the same act: `systemctl restart orchestra` restarts the
  supervisor's children, or `kill -TERM` the gateway alone; never `orchestra down` then `up` (a second
  supervisor).

## 4. Check by effect, right after

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9091/health      # 200
ss -ltn | grep -c '127.0.0.1:9091'                                          # 1 listener
new=$(gw_pid); ps -o ppid= -p "$new"                                         # the supervisor's pid
tr '\0' '\n' < /proc/$new/environ | grep -c -E '^(MENU_SUBMIT_ARMED|WATCH_GATEWAY_TOKEN_FILE)='   # 2
```

Then read `/agents` (bearer from the token file, by reference) for the rows your change should affect
and compare with the reading you took before the kill. Measured on an 8-core box with ~20 seats:
SIGTERM to `/health` 200 in about 16 seconds; the public health endpoint answers 200 as soon as the
local one does.

## 5. Record it

Keep, next to the change: the quiet reading, the old and new pid with the supervisor's pid, the seconds
of outage, the before/after `/agents` rows, and the md5 of the file you changed (plus the one you backed
up), so the next person can roll back by copying the backup and sending the same signal.
