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
    assert [n for n, *_ in SH.SOURCES] == ["profile", "telegram", "learning", "facts"]


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


def test_all_fresh_is_byte_identical_to_base(mod, data, monkeypatch):
    write_profile(data)
    write_telegram(data)
    write_learning(data)
    gated = mod.build_context(calling_channel="text")
    _always_inject(monkeypatch)
    base = mod.build_context(calling_channel="text")
    assert _stable(gated) == _stable(base)
    for head in (PROFILE_HEAD, TELEGRAM_HEAD, RULES_HEAD):
        assert head in gated
    assert "  [preference] likes short answers" in gated
    assert "  [system] deploy finished" in gated
    assert "  - confirm before spawning" in gated


@pytest.mark.parametrize("writer,head,window", [
    (write_profile, PROFILE_HEAD, SH.PROFILE_MAX_AGE),
    (write_telegram, TELEGRAM_HEAD, SH.TELEGRAM_MAX_AGE),
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

def test_knowledge_tool_names_a_missing_facts_store(mod, data):
    out = mod._knowledge_lookup("nobody-in-particular")
    assert out.startswith("No knowledge source for 'nobody-in-particular': the facts store (facts/facts_db.json) is missing.")
    assert "Traceback" not in out and "gm_command" in out


def test_knowledge_tool_keeps_the_old_answer_when_the_store_exists(mod, data):
    write_facts(data)
    out = mod._knowledge_lookup("nobody-in-particular")
    assert out.startswith("No knowledge found for 'nobody-in-particular'.")


def test_knowledge_tool_unreadable_store_is_named_not_raised(mod, data):
    (data / "facts").mkdir()
    (data / "facts" / "facts_db.json").mkdir()
    out = mod._knowledge_lookup("nobody-in-particular")
    assert "is unreadable" in out and "Traceback" not in out


def test_all_fresh_matches_the_base_commit_byte_for_byte(mod, data, tmp_path, monkeypatch):
    """The strongest identity check: the pre-fix build_context itself, read from the base commit,
    against the gated one with every source fresh. Skipped where the base commit is not in the clone
    (a depth-1 CI checkout); the always-inject comparison above runs everywhere."""
    import subprocess
    base_sha = "94ff6791"
    r = subprocess.run(["git", "show", f"{base_sha}:services/arturo/arturo-proxy.py"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout:
        pytest.skip("base commit not in this clone")
    src = tmp_path / "base-arturo-proxy.py"
    src.write_text(r.stdout)
    spec = importlib.util.spec_from_file_location("arturo_proxy_base_copy", src)
    base_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base_mod)
    as_fleet(base_mod)
    write_profile(data)
    write_telegram(data)
    write_learning(data)
    assert _stable(mod.build_context(calling_channel="voice")) == _stable(base_mod.build_context(calling_channel="voice"))
