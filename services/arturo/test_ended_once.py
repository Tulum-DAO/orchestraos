"""RED-first tests — P6 premature "Voice call ended" fix (fold-in msg_9b31854d).

One real call can reach gm as "Voice call ended" up to 3x: (A) watchdog/server-journal
finalize, (B) client durable inject (+re-notify/sweeper), (C) a second ASR-fork server shard.
Per-journal guards exist; nothing spans journals for the same CALL. The fix is a per-call
once-ledger (key = conv_id when stamped, else the journal's own call_id) claimed atomically
at inject time and released on failure so retries still deliver. Flag ARTURO_ENDED_ONCE=1,
default OFF = behavior unchanged.
"""
import importlib.util
import json
import pathlib
import threading
import time

import pytest

from services.arturo import ended_once as eo


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_eo", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ledger(tmp_path):
    return str(tmp_path / "ended-injects.json")


# ---------- ledger semantics ----------

def test_claim_once_then_refused(ledger):
    assert eo.claim("conv_abc", ledger) is True
    assert eo.claim("conv_abc", ledger) is False       # second claim for the same call refused
    assert eo.claim("conv_other", ledger) is True      # other calls unaffected


def test_release_reopens_claim_for_retry(ledger):
    assert eo.claim("conv_abc", ledger)
    eo.release("conv_abc", ledger)                     # inject FAILED -> retry path must stay open
    assert eo.claim("conv_abc", ledger) is True


def test_claim_atomic_under_concurrency(ledger):
    wins = []
    def one():
        if eo.claim("conv_race", ledger):
            wins.append(1)
    threads = [threading.Thread(target=one) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1, "exactly ONE concurrent claimer may win"


def test_prune_old_entries(ledger):
    eo.claim("conv_old", ledger)
    # age the entry past the TTL
    d = json.loads(pathlib.Path(ledger).read_text())
    d["conv_old"] = time.time() - (eo.TTL_DAYS + 1) * 86400
    pathlib.Path(ledger).write_text(json.dumps(d))
    eo.claim("conv_new", ledger)                       # any claim opportunistically prunes
    d = json.loads(pathlib.Path(ledger).read_text())
    assert "conv_old" not in d and "conv_new" in d


def test_flag_off_claim_always_true(ledger, monkeypatch):
    monkeypatch.delenv(eo.FLAG, raising=False)
    assert eo.guard_enabled() is False
    # the proxy call sites consult guard_enabled() and skip the ledger entirely when off


# ---------- proxy wiring: the three dup classes ----------

def _mk_server_journal(mod, dir_, conv_id, n_turns=4, origin="funnel"):
    j = mod._CallJournal(dir=dir_, page="voice")
    for i in range(n_turns):
        j.add_turn("user", f"turn {i} about work")
        j.add_turn("arturo", f"reply {i}")
    d = json.loads(j.path.read_text())
    d["origin"] = origin
    d["conv_id"] = conv_id
    from services.arturo.call_journal import _atomic_write
    _atomic_write(j.path, d)
    return j.path


def _mk_client_journal(mod, dir_, conv_id, injected=False):
    p = dir_ / f"vc_client_{conv_id}.json"
    d = {"call_id": f"vc_client_{conv_id}", "source": "client", "status": "ended",
         "conv_id": conv_id, "origin": "funnel", "gm_injected": injected,
         "summary": "test call", "started_at": time.time() - 60,
         "turns": [{"role": "user", "text": "hello there my friend"},
                   {"role": "arturo", "text": "hey shaw"}]}
    p.write_text(json.dumps(d))
    return p


def _wire(mod, tmp_path, monkeypatch, posts):
    import services.arturo.endcall as ec
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "_GM_INJECT_ENABLED", True)
    monkeypatch.setattr(mod, "_INJECT_ASYNC", False)     # deterministic inline inject
    monkeypatch.setattr(mod, "ENDED_ONCE_LEDGER", tmp_path / "ended-injects.json")
    # count every gm POST; 200 = accepted
    monkeypatch.setattr(ec, "_default_post", lambda s, t: (posts.append(t) or 200))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # endcall blocks posts under pytest; our stub replaces it anyway


def test_dup_class_A_then_B_single_inject(tmp_path, monkeypatch):
    """Watchdog finalizes+injects the server journal (premature end), then the client notify
    lands and would inject the SAME call again -> guard suppresses the second."""
    monkeypatch.setenv(eo.FLAG, "1")
    mod = _load_proxy()
    posts = []
    _wire(mod, tmp_path, monkeypatch, posts)
    sp = _mk_server_journal(mod, tmp_path, "convA")
    mod._finalize_journal_file(sp)                     # A: server inject fires (1st, wins)
    time.sleep(0.3)
    _mk_client_journal(mod, tmp_path, "convA")
    mod.finalize_from_client(client_call_id=f"vc_client_convA", conv_id="convA")  # B: suppressed
    time.sleep(0.3)
    ended = [t for t in posts if "Voice call ended" in t]
    assert len(ended) == 1, f"one real call must inject exactly once, got {len(ended)}"
    # and the client journal is durably marked so the sweeper never re-drives it
    d = json.loads((tmp_path / "vc_client_convA.json").read_text())
    assert d.get("gm_injected") or d.get("gm_inject_suppressed")


def test_dup_class_C_second_shard_suppressed(tmp_path, monkeypatch):
    """Two server journals for the SAME conv (ASR fork): only one ended-inject total."""
    monkeypatch.setenv(eo.FLAG, "1")
    mod = _load_proxy()
    posts = []
    _wire(mod, tmp_path, monkeypatch, posts)
    p1 = _mk_server_journal(mod, tmp_path, "convC", n_turns=6)
    p2 = _mk_server_journal(mod, tmp_path, "convC", n_turns=2)
    mod._finalize_journal_file(p1)
    time.sleep(0.3)
    mod._finalize_journal_file(p2)
    time.sleep(0.3)
    ended = [t for t in posts if "Voice call ended" in t]
    assert len(ended) == 1, f"ASR-fork shards of one call must not double-inject, got {len(ended)}"


def test_failed_inject_keeps_retry_open(tmp_path, monkeypatch):
    """Claim is success-tied: a failed inject releases the claim so the sweeper retry can land."""
    monkeypatch.setenv(eo.FLAG, "1")
    mod = _load_proxy()
    posts = []
    import services.arturo.endcall as ec
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "_GM_INJECT_ENABLED", True)
    monkeypatch.setattr(mod, "_INJECT_ASYNC", False)
    monkeypatch.setattr(mod, "ENDED_ONCE_LEDGER", tmp_path / "ended-injects.json")
    fail_then_ok = {"n": 0}
    def post(s, t):
        fail_then_ok["n"] += 1
        return 502 if fail_then_ok["n"] == 1 else (posts.append(t) or 200)
    monkeypatch.setattr(ec, "_default_post", post)
    _mk_client_journal(mod, tmp_path, "convF")
    mod._attempt_gm_inject(tmp_path / "vc_client_convF.json")   # fails (502)
    d = json.loads((tmp_path / "vc_client_convF.json").read_text())
    assert not d.get("gm_injected")
    mod._attempt_gm_inject(tmp_path / "vc_client_convF.json")   # retry must still be claimable
    ended = [t for t in posts if "Voice call ended" in t]
    assert len(ended) == 1
    d = json.loads((tmp_path / "vc_client_convF.json").read_text())
    assert d.get("gm_injected")


def test_flag_off_wiring_inert(tmp_path, monkeypatch):
    """ARTURO_ENDED_ONCE unset -> both legacy injects fire exactly as today (byte-identical)."""
    monkeypatch.delenv(eo.FLAG, raising=False)
    mod = _load_proxy()
    posts = []
    _wire(mod, tmp_path, monkeypatch, posts)
    sp = _mk_server_journal(mod, tmp_path, "convZ")
    mod._finalize_journal_file(sp)
    time.sleep(0.3)
    _mk_client_journal(mod, tmp_path, "convZ")
    mod.finalize_from_client(client_call_id="vc_client_convZ", conv_id="convZ")
    time.sleep(0.3)
    ended = [t for t in posts if "Voice call ended" in t]
    assert len(ended) == 2, "flag off must preserve today's (duplicating) behavior exactly"
