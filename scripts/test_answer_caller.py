"""answer_caller: who is answering, worked out from the process chain and the HTTP peer (plan Task 7a). Pure: a
fake /proc. The adversarial end-to-end cases are in test_gate_self_answer.py."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import answer_caller as AC  # noqa: E402

PY = ["/usr/bin/python3"]
SVC_GW = PY + ["/opt/oos/scripts/watch_gateway.py"]
SVC_API = ["node", "/opt/oos/api/dist/server.js"]
SVC_PROXY = ["node", "/opt/oos/dashboard-proxy.js"]
SVC_UP = ["/opt/oos/.venv/bin/python", "-m", "orchestra_cli", "up"]
CLAUDE = ["/usr/bin/claude", "-p"]
BASH = ["bash", "-c", "x"]
CLI = PY + ["/opt/oos/scripts/approval.py", "answer"]


class FakeProc(AC.Proc):
    """procs: pid -> (ppid, cmdline, env value or None, exe). tcp: rows. fds: inode -> pid."""

    def __init__(self, procs, tcp=(), fds=None, uid=1000):
        self.procs, self.tcp, self.fds, self._uid = procs, list(tcp), fds or {}, uid

    def available(self):
        return True

    def uid(self):
        return self._uid

    def ppid(self, pid):
        return self.procs[pid][0] if pid in self.procs else None

    def cmdline(self, pid):
        return self.procs[pid][1] if pid in self.procs else []

    def exe(self, pid):
        p = self.procs.get(pid)
        return (p[3] if len(p) > 3 else "") if p else ""

    def env_value(self, pid, key):
        return self.procs[pid][2] if pid in self.procs else None

    def tcp_rows(self):
        return iter(self.tcp)

    def socket_owner(self, inode):
        return self.fds.get(inode)


def chain(*rows):
    """rows top-down: (cmd, env). pid 1 is init; returns (proc, pid of the last row)."""
    procs = {1: (0, ["/sbin/init"], None)}
    pid = 1
    for i, (cmd, env) in enumerate(rows):
        new = 100 + i
        procs[new] = (pid, cmd, env)
        pid = new
    return procs, pid


def test_a_seat_and_everything_it_runs_is_that_agent():
    procs, pid = chain((PY + ["seat_supervisor.py"], None), (CLAUDE, "dev-acme"), (BASH, "dev-acme"),
                       (CLI, "dev-acme"))
    c = AC.of_process(pid, FakeProc(procs))
    assert c.agents == ("dev-acme",) and not c.unknown


def test_unsetting_the_variable_does_not_hide_the_agent():
    procs, pid = chain((CLAUDE, "dev-gen3"), (BASH, "dev-gen3"), (["env", "-u", "X"], "dev-gen3"), (CLI, None))
    assert AC.of_process(pid, FakeProc(procs)).agents == ("dev",)            # -genN stripped


def test_a_person_shell_is_a_person():
    procs, pid = chain((["sshd"], None), (BASH, None), (CLI, None))
    assert AC.of_process(pid, FakeProc(procs)) == AC.Caller((), False, "")


def test_services_answer_for_their_caller_and_inherited_values_are_ignored():
    # orchestra up restarted from gm's shell: every service inherited ORCHESTRA_AGENT_ID=gm
    procs, pid = chain((CLAUDE, "gm"), (BASH, "gm"), (SVC_UP, "gm"), (SVC_API, "gm"), (CLI, "gm"))
    # still found: gm's own claude sits ABOVE the services (gm started them), so an answer from there is gm's
    assert AC.of_process(pid, FakeProc(procs)).agents == ("gm",)
    # the usual shape: orchestra up detached to init, then the API shells the CLI with the inherited value
    procs, pid = chain((SVC_UP, "gm"), (SVC_API, "gm"), (CLI, "gm"))
    assert AC.of_process(pid, FakeProc(procs)).agents == ()
    procs, pid = chain((SVC_UP, None), (SVC_API, None), (CLI, None))
    assert AC.of_process(pid, FakeProc(procs)).agents == ()


def test_an_agent_runtime_below_a_service_counts_even_with_the_same_value():
    procs, pid = chain((SVC_UP, "gm"), (PY + ["seat_supervisor.py"], "gm"), (CLAUDE, "gm"), (CLI, "gm"))
    assert AC.of_process(pid, FakeProc(procs)).agents == ("gm",)
    procs, pid = chain((SVC_UP, "gm"), (PY + ["x.py"], "pm"))                 # a different value: not inherited
    assert AC.of_process(pid, FakeProc(procs)).agents == ("pm",)


def test_nested_agents_nearest_first():
    procs, pid = chain((CLAUDE, "pm"), (BASH, "pm"), (CLAUDE, "helper"), (CLI, "helper"))
    assert AC.of_process(pid, FakeProc(procs)).agents == ("helper", "pm")


def test_a_shell_line_mentioning_a_service_is_not_a_service():
    assert not AC.is_service(["bash", "-c", "grep x scripts/watch_gateway.py; approval.py answer"])
    assert not AC.is_service(["bash", "-c", "orchestra up"])
    assert AC.is_service(SVC_GW) and AC.is_service(SVC_API) and AC.is_service(SVC_PROXY) and AC.is_service(SVC_UP)
    assert AC.is_service(["python3", "scripts/watch_gateway.py"]) and AC.is_proxy(SVC_PROXY)
    assert not AC.is_proxy(["bash", "-c", "node dashboard-proxy.js"])
    assert AC.is_runtime(["/usr/bin/claude"], "/usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe")
    assert AC.is_runtime(["node", "/usr/lib/node_modules/@openai/codex/bin/codex.js"])


def test_no_proc_falls_back_to_own_env(monkeypatch):
    class NoProc(AC.Proc):
        def available(self):
            return False
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev-gen2")
    assert AC.of_process(None, NoProc()).agents == ("dev",)
    assert AC.of_peer("127.0.0.1", 5, 6, proc=NoProc()) == AC.Caller((), False, "no_proc")


# ---- HTTP peers ----

def _net(owner_procs, owner_pid, *, port=50001, server=8888, uid=1000, inode=77, ip="127.0.0.1"):
    return FakeProc(owner_procs, tcp=[(ip, port, "127.0.0.1", server, uid, inode)], fds={inode: owner_pid})


def test_a_local_agent_peer_is_that_agent():
    procs, pid = chain((CLAUDE, "dev"), (["curl", "-s"], "dev"))
    assert AC.of_peer("127.0.0.1", 50001, 8888, proc=_net(procs, pid)).agents == ("dev",)
    assert AC.of_peer("::ffff:127.0.0.1", 50001, 8888, proc=_net(procs, pid)).agents == ("dev",)


def test_a_local_person_peer_is_a_person():
    procs, pid = chain((["chrome"], None))
    assert AC.of_peer("127.0.0.1", 50001, 8888, proc=_net(procs, pid)) == AC.Caller((), False, "")


def test_not_a_local_socket_is_a_remote_device():
    assert AC.of_peer("192.0.2.9", 443, 8890, proc=FakeProc({})) == AC.Caller((), False, "remote")
    assert AC.of_peer("127.0.0.1", 443, 8890, proc=FakeProc({})).unknown         # loopback, yet not there


def test_another_users_socket_is_serve_unless_it_came_from_this_machine():
    p = FakeProc({}, tcp=[("127.0.0.1", 50001, "127.0.0.1", 8890, 0, 9)])
    assert AC.of_peer("127.0.0.1", 50001, 8890, forwarded_for="100.64.0.9", proc=p) == AC.Caller((), False, "other_user")
    assert AC.of_peer("127.0.0.1", 50001, 8890, forwarded_for="1.2.3.4, 127.0.0.1", proc=p).unknown  # last entry
    assert AC.of_peer("127.0.0.1", 50001, 8890, proc=p) == AC.Caller((), False, "other_user")


def test_a_peer_that_cannot_be_found_is_unknown():
    p = FakeProc({}, tcp=[("127.0.0.1", 50001, "127.0.0.1", 8888, 1000, 9)], fds={})
    assert AC.of_peer("127.0.0.1", 50001, 8888, proc=p).unknown


def test_the_proxy_hop_is_followed_once():
    procs, agent_pid = chain((CLAUDE, "dev"), (["curl"], "dev"))
    procs[500] = (1, SVC_PROXY, None)
    tcp = [("127.0.0.1", 50001, "127.0.0.1", 8888, 1000, 70),       # proxy -> API
           ("127.0.0.1", 40001, "127.0.0.1", 8891, 1000, 71)]       # agent -> proxy
    p = FakeProc(procs, tcp=tcp, fds={70: 500, 71: agent_pid})
    assert AC.of_peer("127.0.0.1", 50001, 8888, forwarded_peer="127.0.0.1|40001|8891", proc=p).agents == ("dev",)
    assert AC.of_peer("127.0.0.1", 50001, 8888, proc=p).unknown                       # no header
    assert AC.of_peer("127.0.0.1", 50001, 8888, forwarded_peer="garbage", proc=p).unknown
    # the proxy naming itself cannot loop
    assert AC.of_peer("127.0.0.1", 50001, 8888, forwarded_peer="127.0.0.1|50001|8888", proc=p).unknown


def test_another_service_as_peer_is_unknown():
    procs = {1: (0, ["init"], None), 600: (1, SVC_API, None)}
    p = FakeProc(procs, tcp=[("127.0.0.1", 50001, "127.0.0.1", 8890, 1000, 70)], fds={70: 600})
    assert AC.of_peer("127.0.0.1", 50001, 8890, proc=p).unknown


def test_parse_peer():
    assert AC.parse_peer("127.0.0.1|5|6") == ("127.0.0.1", 5, 6)
    assert AC.parse_peer("::1|5|6") == ("::1", 5, 6)
    for bad in (None, "", "1.2.3.4|0|6", "x|1|2", "1.2.3.4|1", "1.2.3.4|1|2|3", "1.2.3.4|70000|2", "a" * 200):
        assert AC.parse_peer(bad) is None


def test_is_local_address():
    assert AC.is_local_address("127.0.0.1")
    assert not AC.is_local_address("192.0.2.123") and not AC.is_local_address("nope")


def test_real_proc_reads_this_process():
    if not AC.PROC.available():
        pytest.skip("no /proc")
    assert AC.PROC.ppid(os.getpid()) == os.getppid()
    assert AC.PROC.cmdline(os.getpid())


# ---- the rule ----

@pytest.mark.parametrize("caller,handed,why", [
    (AC.Caller(("dev",)), None, "own_card"),
    (AC.Caller(("helper", "dev")), None, "own_card"),             # a helper the held agent started
    (AC.Caller(("dev",)), "dev", "own_card"),                     # even when a rule names the owner
    (AC.Caller(("pm",)), None, "agent_not_handed"),
    (AC.Caller(("pm",)), "qa", "agent_not_handed"),
    (AC.Caller(("helper", "pm")), "pm", "agent_not_handed"),      # the assignee must be the nearest agent
    (AC.Caller(("pm",), True), "pm", "agent_not_handed"),
    (AC.Caller(("pm",)), "pm-gen2", None),                        # handed to pm by a rule
    (AC.Caller((), True), None, "caller_unknown"),
    (AC.Caller(), None, None),                                    # a person
])
def test_gate_refusal(caller, handed, why):
    assert AC.gate_refusal({"dev-gen4", "dev"}, caller, handed_to=handed) == why


# ---- review r1: an incomplete chain is unknown, never a person ----

class GappyProc(FakeProc):
    """procs rows may carry env AC.UNREADABLE; owners: pid -> uid (default ours)."""

    def __init__(self, procs, owners=None, **kw):
        super().__init__(procs, **kw)
        self.owners = owners or {}

    def owner(self, pid):
        return self.owners.get(pid, self._uid)


def test_our_own_unreadable_process_is_unknown():
    procs, pid = chain((CLAUDE, AC.UNREADABLE), (BASH, None), (CLI, None))
    c = AC.of_process(pid, GappyProc(procs))
    assert c.unknown and c.agents == ()
    assert AC.gate_refusal({"dev"}, c) == "caller_unknown"


def test_another_users_unreadable_ancestor_is_fine():
    procs, pid = chain((["sshd"], AC.UNREADABLE), (BASH, None), (CLI, None))
    c = AC.of_process(pid, GappyProc(procs, owners={100: 0}))
    assert not c.unknown and c.agents == ()


def test_a_broken_parent_link_is_unknown():
    procs = {100: (None, BASH, None), 101: (100, CLI, None)}         # 100's parent cannot be read
    c = AC.of_process(101, GappyProc(procs))
    assert c.unknown


def test_our_own_unclassifiable_process_is_unknown():
    class NoCmd(GappyProc):
        def cmdline(self, pid):
            return None if pid == 101 else super().cmdline(pid)
    # a service that inherited gm, then a process we cannot classify (it might be gm's runtime) -> unknown
    procs, pid = chain((SVC_UP, "gm"), (CLAUDE, "gm"), (CLI, "gm"))
    c = AC.of_process(pid, NoCmd(procs))
    assert c.unknown and AC.gate_refusal({"dev"}, c) in ("caller_unknown", "agent_not_handed")
