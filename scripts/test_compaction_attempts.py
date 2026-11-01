"""Failed switches and what the agent knows (amendment 15): every attempt recorded with a plain, redacted reason,
notices that know how they were started, the compact note kept out of everything we show, and the lookups the agent
uses. Neutral fixtures, a temp ORCHESTRA_DIR."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from scripts import episodic_memory as EM


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    return tmp_path


def line(uuid, typ, content, sid, ts="2026-10-31T07:00:00Z"):
    return json.dumps({"type": typ, "uuid": uuid, "sessionId": sid, "timestamp": ts, "message": {"role": typ, "content": content}}) + "\n"


def near_now(stamp: str) -> bool:
    t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    return abs(t - time.time()) < 120


def test_attempts_recorded_updated_listed_newest_first_with_the_clocks_time(box):
    a = EM.record_attempt("qa-demo", "manual", "waiting", "a background build was still running")
    b = EM.record_attempt("qa-demo", "auto", "failed", "the check on the copy failed: it couldn't name your last request",
                          from_session="sess-1")
    EM.record_attempt("dev-demo", "auto", "committed", None, notice_id=3, from_session="sess-d1", to_session="sess-d2")
    rows = EM.attempts("qa-demo")
    assert [r["id"] for r in rows] == [b, a], "newest first, this seat only"
    assert all(near_now(r["at"]) for r in rows), "the time comes from the clock"
    EM.update_attempt(a, "committed", to_session="sess-2")
    got = next(r for r in EM.attempts("qa-demo") if r["id"] == a)
    assert (got["outcome"], got["reason"], got["to_session"]) == ("committed", "a background build was still running", "sess-2")
    assert len(EM.attempts(None)) == 3 and len(EM.attempts("qa-demo", limit=1)) == 1
    for bad in (lambda: EM.record_attempt("qa-demo", "cron", "failed", "x"), lambda: EM.record_attempt("qa-demo", "auto", "lost", "x"),
                lambda: EM.update_attempt(a, "lost")):
        with pytest.raises(ValueError):
            bad()
    with pytest.raises(KeyError):
        EM.update_attempt(999, "failed")


def test_reasons_and_indexed_text_never_hold_secrets(box, tmp_path):
    i = EM.record_attempt("qa-demo", "manual", "failed", "the server at https://sample:hunter2pass@example.com refused token=abcd1234efgh "  # pragma: allowlist secret
                                                          "with key sk-ant-api03-SAMPLEsampleSAMPLEsample")
    r = EM.attempts("qa-demo")[0]
    assert r["id"] == i and "hunter2pass" not in r["reason"] and "abcd1234efgh" not in r["reason"] and "SAMPLEsample" not in r["reason"]
    assert "[redacted: URL password]" in r["reason"] and "[redacted: Anthropic key]" in r["reason"]
    EM.update_attempt(i, "failed", reason="SAMPLE_API_KEY=supersecretvalue was wrong")
    assert "supersecretvalue" not in EM.attempts("qa-demo")[0]["reason"]
    assert EM.redact("commit abababab and uuid 11111111-2222-3333-4444-555555555555 stay") == \
        "commit abababab and uuid 11111111-2222-3333-4444-555555555555 stay"
    sid = "sess-r"
    p = tmp_path / "r.jsonl"
    p.write_text(line("u1", "user", "here: ghp_" + "a" * 36 + " and the Sample notes", sid))
    EM.ingest_session("qa-demo", sid, 1, p)
    assert "ghp_aaaa" not in EM.query("Sample notes")[0]["snippet"]
    assert EM.verify("qa-demo", sid, "[redacted: GitHub token]")[0]


def test_notices_know_their_trigger(box):
    a = EM.notice_start("qa-demo", "sess-1", 100)
    m = EM.notice_start("qa-demo", "sess-1", 100, trigger="manual")
    n = {x["id"]: x for x in EM.notices("qa-demo")}
    assert (n[a]["trigger"], n[m]["trigger"]) == ("auto", "manual")
    with pytest.raises(ValueError):
        EM.notice_start("qa-demo", "sess-1", 100, trigger="cron")
    assert near_now(n[a]["started_at"])


def test_the_compact_note_is_stripped_only_from_registered_entries(box, tmp_path):
    note = ("<compact-note>\nYour earlier request to compact this chat didn't go through: a background build was still "
            "running. Mention it plainly if asked.\n</compact-note>\n")
    sid = "sess-n"
    p = tmp_path / "n.jsonl"
    p.write_text(line("u1", "user", note + "What is next?", sid) + line("u2", "user", note + "I pasted the note myself", sid))
    EM.mark_seen_only(sid, "u1")
    EM.ingest_session("qa-demo", sid, 1, p)
    assert EM.strip_seen(sid, "u1", note + "x") == "x"
    assert EM.strip_seen(sid, "u2", note + "x") == note + "x"
    assert [r["uuid"] for r in EM.query("background build")] == ["u2"], "only the person's own entry keeps the words"
    assert EM.verify("qa-demo", sid, "What is next?")[1]["text"] == "What is next?"
    both = "<earlier-conversation>a</earlier-conversation>\n" + note + "real"
    assert EM.strip_block(both) == "real"


def test_lookups_compaction_status_and_search(box, tmp_path, capsys):
    EM.record_attempt("qa-demo", "manual", "failed", "a background build was still running")
    EM.record_attempt("qa-demo", "auto", "committed", None)
    EM.record_attempt("dev-demo", "auto", "waiting", "someone was still typing")
    lines = [EM.attempt_line(a) for a in EM.attempts("qa-demo")]
    assert lines[1].endswith(" · you typed /compact · didn't go through · a background build was still running")
    assert lines[0].endswith(" · started on its own · compacted")
    assert lines[1][:10].count("-") == 2 and lines[1][16] == "Z", "2026-10-31 07:12Z"
    for q in ("compact", "why did compacting fail", "compaction", "/compact"):
        rows = EM.query(q, agent="qa-demo")
        assert rows and rows[0]["kind"] == "compaction_attempt", q
        assert {r["agent"] for r in rows if r["kind"] == "compaction_attempt"} == {"qa-demo"}
    assert not any(r["kind"] == "compaction_attempt" for r in EM.query("compactor tidy"))
    assert not any(r["kind"] == "compaction_attempt" for r in EM.query("background build"))
    from orchestra_cli.__main__ import main
    assert main(["memory", "compaction-status", "--agent", "qa-demo"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2 and "didn't go through · a background build was still running" in out[1]
    assert main(["memory", "compaction-status"]) == 0
    assert "dev-demo · started on its own · waiting for a quiet moment · someone was still typing" in capsys.readouterr().out
    assert main(["memory", "compaction-status", "--agent", "pm-demo"]) == 0
    assert capsys.readouterr().out.strip() == "no attempts to compact for pm-demo yet"
    assert main(["memory", "search", "compacting", "--agent", "qa-demo"]) == 0
    assert "[compacting] qa-demo · " in capsys.readouterr().out
    assert main(["memory", "compaction-status", "--agent", "qa-demo", "--json", "--limit", "1"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
