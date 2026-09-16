"""RED (gm msg_faf46471, by effect reconciler 01:45:36Z): after the relational-intent codex swap,
resolve_codex_cid('relational-intent') returned 01a08c8c (RETIRED gen1) while canonical gen2
is 01a0a4fe — the same rotated-root predecessor collision closed for claude in 4129e2ef06.
The codex and gemini resolvers pick the NEWEST-mtime store that declares 'You are <seat>'
without consulting the DB. Fix: one runtime-neutral helper (root from seat, generations
retired_at NOT NULL) applied in BOTH resolvers before their newest-first pick; a retired
store is skipped; retired-only => None (never a retired sid); DB error => no exclusion."""
import json
import os
import sys
import time

sys.path.insert(0, "scripts")
from lineage_daemon.wal import ctx_adapters as ca  # noqa: E402

SEAT = "relational-intent"
OLD = "01a08c8c-c0ee-7e40-8e79-8ee7a56612f1"
NEW = "01a0a4fe-30f4-7d42-a175-4c8358982794"


def _rollout(home, sid, mtime):
    d = os.path.join(home, ".codex", "sessions", "2026", "09", "15")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"rollout-2026-09-15T08-15-17-{sid}.jsonl")
    lines = [
        {"type": "session_meta", "payload": {"session_id": sid}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "<environment_context><cwd>/x</cwd>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": f"You are {SEAT}. Read /tmp/agent-init-{SEAT}.md"}]}},
    ]
    with open(p, "w") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
    os.utime(p, (mtime, mtime))
    return p


def _transcript(home, cid, mtime):
    d = os.path.join(home, ".gemini", "antigravity-cli", "brain", cid, ".system_generated", "logs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "transcript.jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "USER_INPUT", "content": f"You are {SEAT}. Read /tmp/x"}) + "\n")
    os.utime(p, (mtime, mtime))
    return p


def test_codex_retired_predecessor_rollout_is_skipped_even_when_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    now = time.time()
    _rollout(str(tmp_path), OLD, now)            # retired gen1, touched LAST (reap-time append)
    _rollout(str(tmp_path), NEW, now - 600)      # live gen2, older mtime
    assert ca.resolve_codex_cid(SEAT, retired_sids_fn=lambda s: {OLD}) == NEW


def test_codex_retired_only_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _rollout(str(tmp_path), OLD, time.time())
    assert ca.resolve_codex_cid(SEAT, retired_sids_fn=lambda s: {OLD}) is None


def test_codex_lookup_error_keeps_previous_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    now = time.time()
    _rollout(str(tmp_path), OLD, now)
    _rollout(str(tmp_path), NEW, now - 600)

    def boom(s):
        raise RuntimeError("db")
    assert ca.resolve_codex_cid(SEAT, retired_sids_fn=boom) == OLD


def test_gemini_retired_predecessor_transcript_is_skipped_even_when_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    now = time.time()
    _transcript(str(tmp_path), "cid-old", now)
    _transcript(str(tmp_path), "cid-new", now - 600)
    assert ca.resolve_gemini_cid(SEAT, retired_sids_fn=lambda s: {"cid-old"}) == "cid-new"


def test_gemini_retired_only_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _transcript(str(tmp_path), "cid-old", time.time())
    assert ca.resolve_gemini_cid(SEAT, retired_sids_fn=lambda s: {"cid-old"}) is None


def test_runtime_neutral_helper_is_the_shared_default():
    assert ca._retired_sids_for_seat is ca._claude_retired_sids or callable(ca._retired_sids_for_seat)


# ---- gen-suffix tolerance (by effect right after 9e565ebff6): the promoted green's rollout
# declares 'You are relational-intent-g2'; the codex/gemini regex rejected the -g2 suffix
# (claude's candidate scan is gen-suffix tolerant), so with gen1 excluded the resolver walked
# down to a Sep-10 ABANDONED rollout (01a08c8b, no generation) — which the reconciler would
# have treated as a sid FIX. The seat's own -gN / -genN declaration IS the seat.

def _rollout_decl(home, sid, mtime, decl):
    d = os.path.join(home, ".codex", "sessions", "2026", "09", "15")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"rollout-2026-09-15T08-15-17-{sid}.jsonl")
    lines = [{"type": "session_meta", "payload": {"session_id": sid}},
             {"type": "response_item", "payload": {"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": f"You are {decl}. Read /tmp/x"}]}}]
    with open(p, "w") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
    os.utime(p, (mtime, mtime))


def test_codex_gen_suffixed_declaration_resolves_for_the_seat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    now = time.time()
    _rollout_decl(str(tmp_path), NEW, now, f"{SEAT}-g2")          # promoted green, newest
    _rollout_decl(str(tmp_path), "01a08c8b-de55-7391-836b-431fc0b50ac4", now - 5 * 86400, SEAT)
    assert ca.resolve_codex_cid(SEAT, retired_sids_fn=lambda s: set()) == NEW


def test_codex_suffix_tolerance_does_not_match_other_seats(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _rollout_decl(str(tmp_path), NEW, time.time(), "gemini-relational-intent-g2")
    assert ca.resolve_codex_cid(SEAT, retired_sids_fn=lambda s: set()) is None
    _rollout_decl(str(tmp_path), OLD, time.time(), f"{SEAT}-dev")
    assert ca.resolve_codex_cid(SEAT, retired_sids_fn=lambda s: set()) is None


def test_gemini_gen_suffixed_declaration_resolves_for_the_seat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".gemini/antigravity-cli/brain/cid-g2/.system_generated/logs"
    d.mkdir(parents=True)
    (d / "transcript.jsonl").write_text(
        json.dumps({"type": "USER_INPUT", "content": f"You are {SEAT}-g2. Read /tmp/x"}) + "\n")
    assert ca.resolve_gemini_cid(SEAT, retired_sids_fn=lambda s: set()) == "cid-g2"
    assert ca.resolve_gemini_cid("relational", retired_sids_fn=lambda s: set()) is None
