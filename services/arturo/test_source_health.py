"""Arturo never injects a stale or missing source.

A read-only audit found build_context reading state/users/operator/profile.json (never written on a
real install), state/telegram-outbound.jsonl (months old, injected as "RECENT") and state/learning.db
(no live writer), and the knowledge tool reading facts/facts_db.json (absent). These tests pin the
fix: fresh = byte-identical to the always-inject path, stale/missing/unreadable = no block (learning.db
stale = dated heading), and a clean knowledge answer when the facts store is missing. Everything runs
inside a tmp ORCHESTRA_DIR / ARTURO_STATE / HOME fence; nothing here touches a live install."""
import importlib.util
import json
import os
import pathlib
import re
import sqlite3
import time

import pytest

from services.arturo import source_health as SH
from services.arturo.conftest import as_fleet

H, D = 3600, 86400


def _fence(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARTURO_STATE", str(tmp_path / "data" / "state" / "arturo"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("ARTURO_GM_INJECT", "0")
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "0")
    monkeypatch.setenv("ORCHESTRA_ARTURO_BRAIN", "api")     # no runtime probe at import
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ARTURO_FACTS_RECALL", raising=False)
    assert all(os.environ[k].startswith(str(tmp_path)) for k in ("ORCHESTRA_DIR", "ARTURO_STATE", "HOME"))
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    return tmp_path / "data"


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_source_health", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return as_fleet(mod)


@pytest.fixture
def data(tmp_path, monkeypatch):
    return _fence(tmp_path, monkeypatch)


@pytest.fixture
def mod(data):
    return _load_proxy()


# --- the sources, as their (dead) writers shaped them -------------------------------------------

def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def write_profile(data, age=0):
    p = data / "state" / "users" / "operator" / "profile.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"implicit_facts": [{"type": "preference", "fact": "likes short answers"}]}))
    _age(p, age)
    return p


def write_telegram(data, age=0):
    p = data / "state" / "telegram-outbound.jsonl"
    p.write_text(json.dumps({"source": "system", "text": "deploy finished"}) + "\n")
    _age(p, age)
    return p


def write_learning(data, age=0):
    p = data / "state" / "learning.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE rules (rule_text TEXT, status TEXT)")
    c.execute("INSERT INTO rules VALUES ('confirm before spawning', 'active')")
    c.commit()
    c.close()
    _age(p, age)
    return p


def write_facts(data):
    p = data / "facts" / "facts_db.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"facts": []}))
    return p


def write_unified(data, age=0):
    p = data / "state" / "unified-conversation.jsonl"
    p.write_text(json.dumps({"channel": "dashboard", "role": "operator", "content": "ship the dream fix"}) + "\n")
    _age(p, age)
    return p


UNIFIED_HEAD = "\nRECENT CROSS-CHANNEL (the operator said these on other interfaces):"
PROFILE_HEAD = "\nLEARNED FROM CONVERSATIONS (implicit memory):"
TELEGRAM_HEAD = "\nRECENT TELEGRAM MESSAGES (you sent these or the system sent these to the operator):"
RULES_HEAD = "\nLEARNED RULES (from past interactions — follow these):"


# --- source_status -------------------------------------------------------------------------------

def test_source_status_each_state(tmp_path):
    p = tmp_path / "f"
    assert SH.source_status(p, 10).state == SH.MISSING
    p.write_text("x")
    assert SH.source_status(p, 10).state == SH.FRESH
    _age(p, 11)
    st = SH.source_status(p, 10)
    assert st.state == SH.STALE and st.age >= 11 and st.max_age == 10 and st.readable and not st.fresh
    assert SH.source_status(p, None).state == SH.FRESH            # no window: cannot go stale
    assert SH.source_status(tmp_path, 10).state == SH.UNREADABLE  # a directory is not a source
    if os.geteuid() != 0:
        p.chmod(0)
        assert SH.source_status(p, 10).state == SH.UNREADABLE
        p.chmod(0o600)


def test_as_of_suffix_only_when_stale(tmp_path):
    p = tmp_path / "f"
    p.write_text("x")
    assert SH.as_of_suffix(SH.source_status(p, 10)) == ""
    _age(p, 2 * D)
    s = SH.as_of_suffix(SH.source_status(p, 10))
    assert s.startswith(" (as of 20") and s.endswith(")")


def test_windows_are_what_the_audit_asked_for():
    assert SH.TELEGRAM_MAX_AGE == 6 * H and SH.LEARNING_MAX_AGE == 7 * D and SH.PROFILE_MAX_AGE == 30 * D
    assert SH.UNIFIED_MAX_AGE == 6 * H
    assert [n for n, *_ in SH.SOURCES] == ["profile", "telegram", "unified", "learning", "facts"]


def test_doctor_rows_one_per_source(data):
    write_profile(data)
    write_telegram(data, age=7 * H)
    (data / "state" / "learning.db").mkdir()            # a directory where a db should be
    rows = {n: (s, d) for n, s, d in SH.doctor_rows(data)}
    assert rows["profile"][0] == SH.FRESH and "window 30d" in rows["profile"][1]
    assert rows["telegram"][0] == SH.STALE and "7h old, window 6h" in rows["telegram"][1] and "skipped" in rows["telegram"][1]
    assert rows["learning"][0] == SH.UNREADABLE and "not injected" in rows["learning"][1]
    assert rows["facts"][0] == SH.MISSING and "facts/facts_db.json missing" in rows["facts"][1]


# --- build_context: fresh is byte-identical to the always-inject path ----------------------------

def _always_inject(monkeypatch):
    """The base behaviour: every read site checked only `exists()`. Forcing every status FRESH
    reproduces it exactly, so a comparison proves the gate adds nothing when all sources are fresh."""
    real = SH.source_status

    def fresh(path, max_age=None, now=None):
        st = real(path, None, now)
        return st
    monkeypatch.setattr(SH, "source_status", fresh)


_CLOCK = re.compile(r"^--- TIME: .* ---$", re.M)


def _stable(ctx):
    # the prompt carries ONE clock line ("--- TIME: <iso> ---"); everything else must match byte for byte
    assert len(_CLOCK.findall(ctx)) == 1
    return _CLOCK.sub("--- TIME: <clock> ---", ctx)


# Every channel, not only text. This replaces a comparison against the pre-fix build_context read from a fixed old
# commit: that one went red on every intended prompt change after it (#701's included) and skipped in CI, whose
# depth-1 checkout has no such commit, so main carried it red unseen. The claim, that the gate adds nothing when every
# source is fresh, is this test's, against today's prompt, in every checkout.
@pytest.mark.parametrize("channel", ["text", "voice", "telegram", "dashboard"])
def test_all_fresh_is_byte_identical_to_base(mod, data, monkeypatch, channel):
    write_profile(data)
    write_telegram(data)
    write_learning(data)
    gated = mod.build_context(calling_channel=channel)
    _always_inject(monkeypatch)
    base = mod.build_context(calling_channel=channel)
    assert _stable(gated) == _stable(base)
    for head in (PROFILE_HEAD, TELEGRAM_HEAD, RULES_HEAD):
        assert head in gated
    assert "  [preference] likes short answers" in gated
    assert "  [system] deploy finished" in gated
    assert "  - confirm before spawning" in gated


@pytest.mark.parametrize("writer,head,window", [
    (write_profile, PROFILE_HEAD, SH.PROFILE_MAX_AGE),
    (write_telegram, TELEGRAM_HEAD, SH.TELEGRAM_MAX_AGE),
    (write_unified, UNIFIED_HEAD, SH.UNIFIED_MAX_AGE),
])
def test_skipped_sources_each_status(mod, data, writer, head, window):
    assert head not in mod.build_context(calling_channel="text")                    # missing
    p = writer(data)
    assert head in mod.build_context(calling_channel="text")                        # fresh
    _age(p, window + 60)
    ctx = mod.build_context(calling_channel="text")                                 # stale
    assert head not in ctx and "as of" not in ctx.split(head.strip()[:10])[0][-40:]
    p.unlink()
    p.mkdir()                                                                       # unreadable
    assert head not in mod.build_context(calling_channel="text")


def test_learning_rules_each_status(mod, data):
    assert "LEARNED RULES" not in mod.build_context(calling_channel="text")         # missing
    p = write_learning(data)
    ctx = mod.build_context(calling_channel="text")                                 # fresh: untouched heading
    assert RULES_HEAD in ctx and "  - confirm before spawning" in ctx
    _age(p, SH.LEARNING_MAX_AGE + 60)
    ctx = mod.build_context(calling_channel="text")                                 # stale: still injected, dated
    assert RULES_HEAD not in ctx
    line = next(l for l in ctx.splitlines() if l.startswith("LEARNED RULES"))
    assert line == "LEARNED RULES (from past interactions — follow these) (as of " + SH.source_status(p).as_of() + "):"
    assert "  - confirm before spawning" in ctx
    p.unlink()
    p.mkdir()                                                                       # unreadable
    assert "LEARNED RULES" not in mod.build_context(calling_channel="text")


def test_missing_sources_leave_no_heading_and_no_empty_block(mod, data):
    ctx = mod.build_context(calling_channel="voice")
    for head in (PROFILE_HEAD, TELEGRAM_HEAD, RULES_HEAD):
        assert head.strip() not in ctx
    assert "\n\n\n" not in ctx.replace("===", "")[-3000:]


# --- the knowledge tool ---------------------------------------------------------------------------

def test_knowledge_tool_names_a_missing_facts_store(mod, data, monkeypatch):
    monkeypatch.setenv("ARTURO_FACTS_RECALL", "1")        # only then is the facts store consulted at all
    out = mod._knowledge_lookup("nobody-in-particular")
    assert out.startswith("No knowledge source for 'nobody-in-particular': the facts store (facts/facts_db.json) is missing.")
    assert "Traceback" not in out and "gm_command" in out


def test_knowledge_tool_keeps_the_old_answer_when_the_store_exists(mod, data):
    write_facts(data)
    out = mod._knowledge_lookup("nobody-in-particular")
    assert out.startswith("No knowledge found for 'nobody-in-particular'.")


def test_knowledge_tool_flag_off_never_blames_a_store_it_did_not_read(mod, data, monkeypatch):
    monkeypatch.delenv("ARTURO_FACTS_RECALL", raising=False)
    out = mod._knowledge_lookup("nobody-in-particular")
    assert out.startswith("No knowledge found for 'nobody-in-particular'.") and "facts_db" not in out


def test_knowledge_tool_unreadable_store_is_named_not_raised(mod, data, monkeypatch):
    monkeypatch.setenv("ARTURO_FACTS_RECALL", "1")
    (data / "facts").mkdir()
    (data / "facts" / "facts_db.json").mkdir()
    out = mod._knowledge_lookup("nobody-in-particular")
    assert "is unreadable" in out and "Traceback" not in out


# --- safe_import: a prompt-source reader that fails to import must never fail a turn --------------

def test_safe_import_returns_module_or_none(tmp_path, monkeypatch):
    assert SH.safe_import("services.arturo.source_health") is SH
    assert SH.safe_import("services.arturo.no_such_reader_module") is None
    pkg = tmp_path / "sh_broken_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bad.py").write_text("raise RuntimeError('boom at import')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert SH.safe_import("sh_broken_pkg.bad") is None       # an exception raised at import, not just absence


# --- running_flag: a flag as the RUNNING proxy has it, not as doctor's shell has it ----------------

def _proc(tmp_path, pid, environ: dict, cmdline=("python3", "/opt/orchestra/services/arturo/arturo-proxy.py")):
    d = tmp_path / "proc" / str(pid)
    d.mkdir(parents=True)
    (d / "environ").write_bytes("\0".join(f"{k}={v}" for k, v in environ.items()).encode() + b"\0")
    (d / "cmdline").write_bytes("\0".join(cmdline).encode() + b"\0")
    return tmp_path / "proc"


def test_running_flag_refuses_a_reused_pid(data, tmp_path):
    """The supervisor's recorded pid now belongs to sshd: its environ is not the proxy's."""
    (data / "state" / "supervisor.json").write_text(json.dumps({"pid": 1, "children": {"arturo": {"pid": 4242}}}))
    proc = _proc(tmp_path, 4242, {"ARTURO_CONTEXT_FILES": "WRONG-PROCESS"}, cmdline=("/usr/sbin/sshd", "-D"))
    assert SH.running_flag("ARTURO_CONTEXT_FILES", data, env={}, proc_root=proc) == (None, "shell (pid reused)")
    assert SH.running_flag("ARTURO_CONTEXT_FILES", data, env={"ARTURO_CONTEXT_FILES": "1"}, proc_root=proc) == ("1", "shell (pid reused)")


def test_running_flag_prefers_the_proxy_environ(data, tmp_path):
    (data / "state" / "supervisor.json").write_text(json.dumps({"pid": 1, "children": {"arturo": {"pid": 4242}}}))
    proc = _proc(tmp_path, 4242, {"PATH": "/usr/bin", "ARTURO_CONTEXT_FILES": "1"})
    assert SH.running_flag("ARTURO_CONTEXT_FILES", data, env={}, proc_root=proc) == ("1", "proxy")
    # the proxy is running WITHOUT the flag: that is the answer, even when doctor's shell has it
    assert SH.running_flag("ARTURO_FACTS_RECALL", data, env={"ARTURO_FACTS_RECALL": "1"}, proc_root=proc) == (None, "proxy")


def test_running_flag_falls_back_to_shell_then_unset(data, tmp_path):
    assert SH.running_flag("X_FLAG", data, env={"X_FLAG": "1"}, proc_root=tmp_path / "noproc") == ("1", "shell")
    (data / "state" / "supervisor.json").write_text(json.dumps({"pid": 1, "children": {"arturo": {"pid": 99999}}}))
    assert SH.running_flag("X_FLAG", data, env={}, proc_root=tmp_path / "noproc") == (None, "unset")   # dead pid
    (data / "state" / "supervisor.json").write_text("{not json")
    assert SH.running_flag("X_FLAG", data, env={"X_FLAG": "0"}, proc_root=tmp_path / "noproc") == ("0", "shell")


# --- the proxy survives a broken source_health: behaves as before the gate (exists() only) ---------

def test_proxy_without_source_health_behaves_as_before(data, monkeypatch):
    import sys
    import services.arturo as _pkg
    monkeypatch.setitem(sys.modules, "services.arturo.source_health", None)   # import -> ImportError
    monkeypatch.delattr(_pkg, "source_health", raising=False)                 # `from pkg import x` checks this first
    mod = _load_proxy()
    assert mod._source_health is not SH and mod._source_health.__doc__.startswith("The pre-gate")   # the fallback is in
    write_profile(data, age=SH.PROFILE_MAX_AGE + 60)      # stale: the fallback cannot tell, injects as before
    write_learning(data, age=SH.LEARNING_MAX_AGE + 60)
    ctx = mod.build_context(calling_channel="text")
    assert PROFILE_HEAD in ctx and RULES_HEAD in ctx and "(as of" not in ctx.split("LEARNED RULES")[1][:60]
    assert TELEGRAM_HEAD not in ctx                        # missing is still missing
    monkeypatch.setenv("ARTURO_FACTS_RECALL", "1")
    assert mod._knowledge_lookup("nobody-in-particular").startswith("No knowledge source for")


def test_proxy_survives_a_source_health_that_raises_at_import(data, monkeypatch):
    """Not just ImportError: a SyntaxError or a RuntimeError raised while importing the gate module
    must also leave the proxy running on the pre-gate behaviour."""
    import sys
    import services.arturo as _pkg

    class _Boom:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "services.arturo.source_health":
                raise RuntimeError("boom at import")
            return None
    monkeypatch.delitem(sys.modules, "services.arturo.source_health", raising=False)
    monkeypatch.delattr(_pkg, "source_health", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Boom()] + sys.meta_path)
    mod = _load_proxy()
    assert mod._source_health.__doc__.startswith("The pre-gate")
    write_telegram(data)
    assert TELEGRAM_HEAD in mod.build_context(calling_channel="text")


def test_a_four_day_old_cross_channel_log_is_not_injected_as_recent(mod, data):
    # the shared unified log's last writer stopped 2026-11-22; every relay call still read its tail as "RECENT"
    p = write_unified(data, age=4 * D)
    ctx = mod.build_context(calling_channel="text")
    assert UNIFIED_HEAD not in ctx and "ship the dream fix" not in ctx
    _age(p, 60)
    ctx = mod.build_context(calling_channel="text")
    assert UNIFIED_HEAD in ctx and "  [dashboard:operator] ship the dream fix" in ctx
