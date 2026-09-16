# test_resolve_providers.py — impure candidate gatherers (spec §3.1 field map). Injected callables.
from services.arturo import resolve_providers as P
from services.arturo import resolve as R


def _fake_gw(pending=None, agents=None):
    def gw(path):
        if path == "/pending-approvals":
            return {"ok": True, "pending": pending or []}
        if path == "/agents":
            return {"ok": True, "agents": agents or []}
        return None
    return gw


# ---- pure entity builders ----

def test_approval_entity_namespaced_and_hydrated():
    row = {"id": "apr_ab12", "from_agent": "pm-adaptiv-payments",
           "question": "Deploy adaptiv payments to prod?", "feature": "payments-deploy",
           "created_at": "2026-08-12T10:00:00+00:00"}
    e = P.approval_entity(row)
    assert e["kind"] == "approval"
    assert e["id"] == "approval:apr_ab12"           # namespaced, ledger id preserved
    assert e["from_agent"] == "pm-adaptiv-payments"
    assert e["feature"] == "payments-deploy"
    assert e["pending"] is True
    assert e["recency_ts"] > 0                        # ISO created_at → epoch
    # salient nouns became inferred aliases
    aliases = {a["alias"] for a in e["aliases"]}
    assert any("adaptiv" in a for a in aliases)
    assert all(a["confidence"] == "inferred" for a in e["aliases"])


def test_agent_entity_uses_session_and_recency():
    row = {"id": "pm-adaptiv-payments", "tmux_session": "pm-adaptiv-payments",
           "state": "working", "last_used_ts": 1_700_000_000.0, "has_pending_menu": True}
    e = P.agent_entity(row)
    assert e["id"] == "agent:pm-adaptiv-payments"
    assert e["recency_ts"] == 1_700_000_000.0
    assert e["pending"] is True


def test_content_entity_digests_output():
    text = "Here are my 5 recommendations for acme:\n1. Ship the explorer\n2. Fix SSR"
    e = P.content_entity("gm", text, now=2_000_000.0)
    assert e["kind"] == "content"
    assert e["id"].startswith("content:gm:")
    assert e["from_agent"] == "gm"
    assert e["recency_ts"] == 2_000_000.0
    aliases = {a["alias"] for a in e["aliases"]}
    assert "recommendations" in aliases


# ---- gather_candidates (union of live approvals + agents + content) ----

def test_gather_unions_all_sources():
    gw = _fake_gw(
        pending=[{"id": "apr_1", "from_agent": "gm", "question": "Ship it?",
                  "created_at": "2026-08-12T10:00:00+00:00"}],
        agents=[{"id": "gm", "tmux_session": "gm", "state": "working",
                 "last_used_ts": 1_700_000_000.0}],
    )
    def get_output(sess):
        return "GM: here are the recommendations and options" if sess == "gm" else ""
    cands = P.gather_candidates(gw, get_output=get_output, focused_agent="gm",
                                now=2_000_000.0)
    kinds = {c["kind"] for c in cands}
    assert kinds == {"approval", "agent", "content"}
    # every id is namespaced type-prefixed (registry-swappable seam)
    assert all(":" in c["id"] for c in cands)


def test_gather_content_only_for_focused_and_named():
    gw = _fake_gw(agents=[{"id": "a1", "tmux_session": "a1", "state": "working"},
                          {"id": "a2", "tmux_session": "a2", "state": "working"}])
    calls = []
    def get_output(sess):
        calls.append(sess)
        return "some output text here"
    P.gather_candidates(gw, get_output=get_output, focused_agent="a1",
                        named_agents=["a2"], now=1.0)
    # bounded: only focused + explicitly named agents get an output scan (never full fleet)
    assert set(calls) == {"a1", "a2"}


def test_gather_degrades_when_gateway_down():
    def gw(path):
        return None
    cands = P.gather_candidates(gw, now=1.0)
    assert cands == []


def test_end_to_end_acceptance_through_providers():
    # §0a: gathered candidates + resolve() → GM content match in one shot
    gw = _fake_gw(
        pending=[{"id": "apr_x", "from_agent": "hamilton-dev",
                  "question": "Deploy hamilton site?",
                  "created_at": "2026-08-12T09:00:00+00:00"}],
        agents=[{"id": "gm", "tmux_session": "gm", "state": "working",
                 "last_used_ts": 1_999_999_700.0}],
    )
    def get_output(sess):
        return ("Here are my 5 recommendations / options for acme strategy going forward"
                if sess == "gm" else "")
    now = 2_000_000_000.0
    cands = P.gather_candidates(gw, get_output=get_output, focused_agent="gm", now=now)
    res = R.resolve("go through the options you suggested for the recommendations", cands, now)
    assert res["status"] == "match"
    assert res["entity"]["kind"] == "content"
    assert res["entity"]["from_agent"] == "gm"
