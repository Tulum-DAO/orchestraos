"""answer_caller.py — who is answering a card: a person, or an agent (and which one).

Only a person may answer a safety-gate card (a permission card or a grant proposal); the agent whose call is held,
and the agent that proposed a grant, never may (plan Task 7a). The answer paths carry no proof of who is calling:
`approval.py answer --answered-by operator` is the caller's own word, and on loopback every API caller is the
operator. So the caller is worked out from the operating system instead.

1. The process chain. From a pid up to pid 1, each process's ORCHESTRA_AGENT_ID as it was STARTED
   (/proc/<pid>/environ). An agent can unset the variable for the commands it runs, but not in its own runtime
   process, which the seat supervisor started with it. Two exceptions keep a person's answer a person's:
   - OrchestraOS services (the gateway, the API, the dashboard proxy, `orchestra up`) are never read: they act
     for an authenticated caller and check that caller themselves;
   - a value a process only inherited from the nearest service above it (the same value, with no agent runtime
     in between) is ignored, so a service restarted from an agent's shell does not make every answer that
     agent's.
2. The HTTP peer. A connection from this machine is found in /proc/net/tcp{,6}, its owner in /proc/*/fd, and then
   that owner's chain (1). The dashboard proxy names its own client in the `x-orchestra-peer` header, followed one
   more hop. A socket owned by another Unix user is Tailscale Serve: a remote device, unless Serve's
   X-Forwarded-For says the request came from this machine. Anything that cannot be seen is `unknown`.

This is a SEATBELT, not a sandbox: every agent runs as the same Unix user, and one that deliberately detaches from
its process tree (setsid, at, systemd-run, a tmux window) or writes the database directly is not stopped. It stops
an agent answering its own card by habit or by mistake through any ordinary path. Without /proc (macOS) only the
process's own environment is read, and HTTP peers count as people.

Stdlib only; every /proc read goes through `Proc`, which tests replace.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass

AGENT_ENV = "ORCHESTRA_AGENT_ID"
PEER_HEADER = "x-orchestra-peer"
_GEN = re.compile(r"-gen\d+$")
_RUNTIME = re.compile(r"^(claude|codex|grok|gemini|agy)(\.exe|\.js)?$")
_SERVICE_FILES = ("scripts/watch_gateway.py", "api/dist/server.js", "dashboard-proxy.js")
_PROXY_FILE = "dashboard-proxy.js"
_INTERP = re.compile(r"^(python(3(\.\d+)?)?|node(js)?)$")
_MAX_CHAIN = 64


def base(agent) -> str:
    """The agent's base id: a -genN rotation suffix stripped."""
    return _GEN.sub("", str(agent or "").strip())


@dataclass(frozen=True)
class Caller:
    agents: tuple = ()          # base agent ids, nearest first
    unknown: bool = False       # a caller on this machine that could not be seen (fail closed on gate cards)
    via: str = ""

    @property
    def nearest(self):
        return self.agents[0] if self.agents else None

    def merged(self, other: "Caller | None") -> "Caller":
        if other is None:
            return self
        agents = tuple(dict.fromkeys(self.agents + other.agents))
        via = "+".join(v for v in (self.via, other.via) if v)
        return Caller(agents, self.unknown or other.unknown, via)


PERSON = Caller()
UNREADABLE = object()       # Proc.env_value: the environment exists but cannot be read


class Proc:
    """The /proc reader. Every method returns None (or empty) when it cannot read."""

    root = "/proc"

    def available(self) -> bool:
        return os.path.isdir(os.path.join(self.root, "self"))

    def uid(self) -> int:
        return os.getuid()

    def ppid(self, pid: int):
        try:
            with open(f"{self.root}/{pid}/stat", "rb") as f:
                s = f.read().decode("utf-8", "replace")
            return int(s[s.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return None

    def cmdline(self, pid: int):
        """The argv, or None when it cannot be read."""
        try:
            with open(f"{self.root}/{pid}/cmdline", "rb") as f:
                return [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            return None

    def exe(self, pid: int) -> str:
        try:
            return os.readlink(f"{self.root}/{pid}/exe")
        except OSError:
            return ""

    def owner(self, pid: int):
        """Who owns the process's environ file: its uid, except root for another user's process and for a
        non-dumpable one (sshd's session process). None when it is gone."""
        try:
            return os.stat(f"{self.root}/{pid}/environ").st_uid
        except OSError:
            return None

    def env_value(self, pid: int, key: str):
        """The variable's value; None when it is absent; UNREADABLE when the environment cannot be read."""
        try:
            with open(f"{self.root}/{pid}/environ", "rb") as f:
                data = f.read()
        except OSError:
            return UNREADABLE
        prefix = key.encode() + b"="
        for item in data.split(b"\0"):
            if item.startswith(prefix):
                v = item[len(prefix):].decode("utf-8", "replace").strip()
                return v or None
        return None

    def tcp_rows(self):
        """(local_ip, local_port, remote_ip, remote_port, uid, inode) for every TCP socket on this machine."""
        for name in ("tcp", "tcp6"):
            try:
                with open(f"{self.root}/net/{name}") as f:
                    lines = f.read().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue
                try:
                    lip, lport = _hexaddr(parts[1])
                    rip, rport = _hexaddr(parts[2])
                    yield lip, lport, rip, rport, int(parts[7]), int(parts[9])
                except ValueError:
                    continue

    def socket_owner(self, inode: int):
        """The pid of a process (of this user) holding the socket, else None."""
        target = f"socket:[{inode}]"
        try:
            pids = [p for p in os.listdir(self.root) if p.isdigit()]
        except OSError:
            return None
        for p in pids:
            fd_dir = f"{self.root}/{p}/fd"
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue
            for fd in fds:
                try:
                    if os.readlink(f"{fd_dir}/{fd}") == target:
                        return int(p)
                except OSError:
                    continue
        return None


PROC = Proc()


def _hexaddr(s: str):
    h, port = s.split(":")
    raw = bytes.fromhex(h)
    if len(raw) == 4:
        ip = ipaddress.IPv4Address(raw[::-1])
    elif len(raw) == 16:
        ip = ipaddress.IPv6Address(b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))
    else:
        raise ValueError("bad address")
    return _norm_ip(str(ip)), int(port, 16)


def _norm_ip(ip):
    """A canonical address string; an IPv4-mapped IPv6 address becomes its IPv4 form. None when not an address."""
    try:
        a = ipaddress.ip_address(str(ip).strip().strip("[]"))
    except ValueError:
        return None
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(a)


def _script_arg(cmd, names) -> bool:
    """The service script is the program's own first argument (`node …/server.js`, `python …/watch_gateway.py`),
    never merely mentioned in a shell command line."""
    cmd = list(cmd or [])
    if len(cmd) < 2 or not _INTERP.match(os.path.basename(cmd[0])):
        return False
    first = next((t for t in cmd[1:] if not t.startswith("-")), "")
    return any(first == n or first.endswith("/" + n) for n in names)


def is_service(cmd) -> bool:
    if _script_arg(cmd, _SERVICE_FILES):
        return True
    toks = [os.path.basename(t) for t in (cmd or [])[:4]]
    for i, t in enumerate(toks[:-1]):
        if t in ("orchestra_cli", "orchestra") and toks[i + 1] == "up":
            return True
    return False


def is_proxy(cmd) -> bool:
    return _script_arg(cmd, (_PROXY_FILE,))


def is_runtime(cmd, exe="") -> bool:
    names = [os.path.basename(exe or "")] + [os.path.basename(t) for t in (cmd or [])[:2]]
    return any(_RUNTIME.match(n) for n in names if n)


def of_process(pid=None, proc: Proc | None = None) -> Caller:
    """The agents in a process's chain (nearest first)."""
    proc = proc or PROC
    if not proc.available():
        v = os.environ.get(AGENT_ENV) if pid is None else None
        return Caller((base(v),), False, "env") if v else Caller((), False, "no_proc")
    chain, seen, gaps = [], set(), []
    me = proc.uid()
    p = pid if pid is not None else os.getpid()
    while True:
        if p in seen or len(chain) >= _MAX_CHAIN:
            gaps.append("chain_loop")
            break
        seen.add(p)
        cmd = proc.cmdline(p)
        if cmd is None:
            # Unclassifiable (service? runtime?): for one of our own processes that is a gap (fail closed).
            if proc.owner(p) == me:
                gaps.append(f"cmdline_unreadable:{p}")
            cmd = []
        val = proc.env_value(p, AGENT_ENV)
        if val is UNREADABLE:
            # Another user's process (init, sshd, tailscaled) or a non-dumpable one (its environ file is root's)
            # is not an agent runtime of ours; one of OUR ordinary processes whose environment cannot be read
            # could be (fail closed).
            if proc.owner(p) == me:
                gaps.append(f"env_unreadable:{p}")
            val = None
        chain.append((cmd, proc.exe(p), val))
        if p == 1:
            break
        nxt = proc.ppid(p)
        if nxt is None or nxt <= 0:
            if nxt != 0:                        # pid 0 is the kernel: a normal top of the chain
                gaps.append(f"parent_unreadable:{p}")
            break
        p = nxt
    found = []
    svc_val, runtime_since_svc, under_svc = None, False, False
    for cmd, exe, val in reversed(chain):              # from the top (init) down to the process itself
        if is_service(cmd):
            svc_val, runtime_since_svc, under_svc = val, False, True
            continue
        if is_runtime(cmd, exe):
            runtime_since_svc = True
        if not val:
            continue
        if under_svc and val == svc_val and not runtime_since_svc:
            continue                                   # only inherited from the service above it
        found.append(base(val))
    agents = tuple(dict.fromkeys(reversed(found)))
    if gaps:
        return Caller(agents, True, "incomplete_chain:" + ",".join(gaps[:3]))
    return Caller(agents, False, "process" if agents else "")


def self_caller() -> Caller:
    """This process's own chain (the core calls it for every gate-card answer; tests replace it)."""
    try:
        return of_process()
    except Exception:  # noqa: BLE001 — cannot read: unknown, the safe side
        return Caller((), True, "process_unreadable")


def is_local_address(ip) -> bool:
    """True when `ip` is an address of this machine (it can be bound)."""
    ip = _norm_ip(ip)
    if ip is None:
        return False
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        s = socket.socket(fam, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        s.bind((ip, 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


def parse_peer(spec):
    """'<ip>|<port>|<server port>' -> (ip, port, server_port), else None."""
    if not isinstance(spec, str) or len(spec) > 120:
        return None
    parts = spec.strip().split("|")
    if len(parts) != 3:
        return None
    ip = _norm_ip(parts[0])
    try:
        port, sport = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if ip is None or not (0 < port < 65536 and 0 < sport < 65536):
        return None
    return ip, port, sport


def _last_forwarded(xff):
    if not isinstance(xff, str) or not xff.strip():
        return None
    return xff.split(",")[-1].strip() or None


def of_peer(ip, port, server_port, *, forwarded_peer=None, forwarded_for=None, proc: Proc | None = None,
            _depth=0) -> Caller:
    """Who is on the other end of an HTTP connection to `server_port`."""
    proc = proc or PROC
    if not proc.available():
        return Caller((), False, "no_proc")
    ip = _norm_ip(ip)
    try:
        port, server_port = int(port), int(server_port)
    except (TypeError, ValueError):
        return Caller((), True, "peer_unreadable")
    if ip is None:
        return Caller((), True, "peer_unreadable")
    row = next((r for r in proc.tcp_rows() if r[0] == ip and r[1] == port and r[3] == server_port), None)
    if row is None:
        if is_local_address(ip):
            return Caller((), True, "peer_not_found")    # this machine, but the connection is not there
        return Caller((), False, "remote")
    uid, inode = row[4], row[5]
    if uid != proc.uid():
        ff = _last_forwarded(forwarded_for)
        if ff and is_local_address(ff):
            return Caller((), True, "serve_from_this_machine")
        return Caller((), False, "other_user")
    pid = proc.socket_owner(inode)
    if pid is None:
        return Caller((), True, "peer_not_found")
    cmd = proc.cmdline(pid)
    if cmd is None:
        return Caller((), True, "peer_unreadable")
    if is_proxy(cmd):
        nxt = parse_peer(forwarded_peer) if _depth == 0 else None
        if nxt is None:
            return Caller((), True, "proxy_without_peer")
        return of_peer(*nxt, forwarded_for=forwarded_for, proc=proc, _depth=_depth + 1).merged(
            Caller((), False, "proxy"))
    if is_service(cmd):
        return Caller((), True, "service_peer")       # it may be forwarding anyone; it does not say whom
    return of_process(pid, proc)


def of_peer_spec(spec, *, forwarded_peer=None, forwarded_for=None, proc: Proc | None = None) -> Caller:
    """of_peer over a '<ip>|<port>|<server port>' spec (what the API passes the CLI). A bad spec is unknown."""
    p = parse_peer(spec)
    if p is None:
        return Caller((), True, "peer_unreadable")
    return of_peer(*p, forwarded_peer=forwarded_peer, forwarded_for=forwarded_for, proc=proc)


def gate_refusal(owners, caller: Caller, handed_to=None):
    """Why this caller may not answer a gate card whose owners are `owners`, else None.

    own_card          the caller is (or runs under) the held agent or the grant's proposer
    agent_not_handed  the caller is another agent, and no timeout rule handed it this card
    caller_unknown    a caller on this machine that could not be seen (fail closed)"""
    owners = {base(o) for o in owners if o}
    if owners & set(caller.agents):
        return "own_card"
    if caller.agents:
        h = base(handed_to) if handed_to else None
        if h and caller.nearest == h and h not in owners and not caller.unknown:
            return None
        return "agent_not_handed"
    if caller.unknown:
        return "caller_unknown"
    return None


REFUSAL_TEXT = {
    "own_card": "it is your own card (the held action, or the grant you proposed)",
    "agent_not_handed": ("agents don't answer permission cards; a person does (if you are the person, the "
                         "gateway or API may have been started from an agent's shell: restart `orchestra up` from "
                         "your own terminal)"),
    "caller_unknown": "OrchestraOS could not tell who is answering, so it is treated as an agent",
}
