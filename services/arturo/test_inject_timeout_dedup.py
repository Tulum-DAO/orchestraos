"""RED-first (gm msg_f9106d0c): the call-ended notice for vc_client_f18b68944d8d reached gm 9
times. The gateway POST times out at 10 s under load (urlopen raises 'timed out'), but the
gateway HAS delivered: each delivery leaves /tmp/agent-inject-*.md carrying our marker. The
timeout was logged as failure and the sweeper re-injected every ~5 min. A timed-out POST whose
marker shows up in a fresh inject file is a delivery, not a failure."""
import os
import time

from services.arturo import endcall

MARK = "[voice-call: vc_client_abc123 /x/vc_client_abc123.json]"


def _timeout_post(session, text):
    raise TimeoutError("timed out")


def test_timed_out_post_with_delivery_evidence_counts_as_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(endcall, "INJECT_EVIDENCE_GLOB", str(tmp_path / "agent-inject-*.md"))
    (tmp_path / "agent-inject-1-x.md").write_text("Voice call ended ...\n" + MARK)
    ok, att = endcall.inject_to_gm("gm", "s", MARK, post=_timeout_post, base_delay=0)
    assert ok is True and att == 1


def test_timed_out_post_without_evidence_is_not_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(endcall, "INJECT_EVIDENCE_GLOB", str(tmp_path / "agent-inject-*.md"))
    (tmp_path / "agent-inject-1-x.md").write_text("some other call")
    ok, _ = endcall.inject_to_gm("gm", "s", MARK, post=_timeout_post, base_delay=0)
    assert ok is False


def test_old_inject_file_is_not_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(endcall, "INJECT_EVIDENCE_GLOB", str(tmp_path / "agent-inject-*.md"))
    f = tmp_path / "agent-inject-1-x.md"
    f.write_text(MARK)
    old = time.time() - 3600
    os.utime(f, (old, old))
    ok, _ = endcall.inject_to_gm("gm", "s", MARK, post=_timeout_post, base_delay=0)
    assert ok is False, "an hour-old file proves an EARLIER delivery, not this attempt"


def test_retry_after_an_ambiguous_delivery_does_not_post_again(tmp_path, monkeypatch):
    """The sweeper's retry ~5 min later: the earlier 'timed out' attempt DID deliver. Evidence
    newer than the call's end means: do not post again."""
    monkeypatch.setattr(endcall, "INJECT_EVIDENCE_GLOB", str(tmp_path / "agent-inject-*.md"))
    f = tmp_path / "agent-inject-1-x.md"
    f.write_text(MARK)
    five_min_ago = time.time() - 300
    os.utime(f, (five_min_ago, five_min_ago))
    posts = []
    ok, att = endcall.inject_to_gm("gm", "s", MARK, post=lambda s, t: posts.append(1) or 200,
                                   base_delay=0, since=five_min_ago - 60)
    assert ok is True and posts == [], "already delivered since the call ended: no second post"
