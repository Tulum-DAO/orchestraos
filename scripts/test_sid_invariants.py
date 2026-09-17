"""sid_invariants suite — both directions per invariant, sandboxed.

Every fixture is built inside tmp_path (never the live stores — test isolation
is production protection). The cross-project-dir fixture is MANDATORY per gm's
attribution correction: the invariant was always right; the implementer's
repair pass searched one dir and manufactured a false death. So the trap gets
pinned by a test, in the doing, where it lives.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sid_invariants as S  # noqa: E402


def _transcript(projects_root, project_dir, sid, declares=None,
                summary_mentions=None):
    d = projects_root / project_dir
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "queue-operation"}]
    if summary_mentions:
        lines.append({"type": "user", "isCompactSummary": True, "message": {
            "content": "This session is being continued from a previous "
                       f"conversation. You are {summary_mentions} handled the "
                       "rotation."}})
    if declares:
        lines.append({"type": "user", "message": {
            "content": f"You are {declares}, a T2 agent. Read your prompt."}})
    lines.append({"type": "assistant", "message": {
        "content": [{"type": "text", "text": "Understood."}]}})
    p = d / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


@pytest.fixture
def world(tmp_path):
    (tmp_path / "projects").mkdir()
    return tmp_path / "projects"


def _check(sessions, registry, world):
    return S.check(sessions, registry, projects_root=world,
                   check_resolvers=False)   # INV7 has its own suite


# ---- INV1 both directions ---------------------------------------------------

def test_inv1_duplicate_sid_caught(world):
    _transcript(world, "-p-a", "sid1", declares="alpha")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "beta": {"session_id": "sid1", "project_dir": str(world / "-p-a")}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV1"] == 1
    assert r["findings"]["INV1"][0]["rows"] == ["alpha", "beta"]


def test_inv1_distinct_sids_clean(world):
    _transcript(world, "-p-a", "sid1", declares="alpha")
    _transcript(world, "-p-a", "sid2", declares="beta")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "beta": {"session_id": "sid2", "project_dir": str(world / "-p-a")}}
    assert _check(sessions, {}, world)["counts"]["INV1"] == 0


def test_inv1_lineage_rename_is_exempt_not_violation(world):
    """A canonical name legitimately holding the current generation's session
    (what `gm` does) must not read as a crossed row."""
    _transcript(world, "-p-a", "sid1", declares="gm")
    sessions = {"gm": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "gm-gen13": {"session_id": "sid1", "project_dir": str(world / "-p-a")}}
    registry = {"gm": {"lineage_root": "gm"}, "gm-gen13": {"lineage_root": "gm"}}
    r = _check(sessions, registry, world)
    assert r["counts"]["INV1"] == 0
    assert any(x["invariant"] == "INV1" for x in r["info"]["lineage_exempt"])


def test_inv1_shared_name_word_is_NOT_exempt(world):
    """Exemption must come from DECLARED registry lineage, never a name
    prefix — otherwise any two agents sharing a word bless each other."""
    _transcript(world, "-p-a", "sid1", declares="acme-dev")
    sessions = {"acme-dev": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "acme-web": {"session_id": "sid1", "project_dir": str(world / "-p-a")}}
    assert _check(sessions, {}, world)["counts"]["INV1"] == 1


# ---- INV2 both directions ---------------------------------------------------

def test_inv2_registry_disagreement_caught(world):
    _transcript(world, "-p-a", "sid1", declares="alpha")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-a")}}
    registry = {"alpha": {"session_id": "sid-OTHER"}}
    r = _check(sessions, registry, world)
    assert r["counts"]["INV2"] == 1
    assert r["findings"]["INV2"][0]["registry"] == "sid-OTHER"


def test_inv2_agreement_clean(world):
    _transcript(world, "-p-a", "sid1", declares="alpha")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-a")}}
    assert _check(sessions, {"alpha": {"session_id": "sid1"}}, world)["counts"]["INV2"] == 0


# ---- INV3: THE IMPLEMENTER TRAP (cross-project-dir) -------------------------

def test_inv3_transcript_in_a_DIFFERENT_project_dir_is_alive(world):
    """gm's false death: client-page-access's session lived under its own
    repo's project dir. A one-dir search calls it dead and destroys a valid
    pointer. Transcripts are CWD-SCOPED — the search must glob every dir."""
    _transcript(world, "-home-user-repos-acme-client-page-access", "sidX",
                declares="client-page-access")
    sessions = {"client-page-access": {
        "session_id": "sidX",
        "project_dir": str(world / "-home-user-repos-acme-client-page-access")}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV3"] == 0, "manufactured a FALSE DEATH"
    assert S.find_transcript("sidX", world) is not None


def test_inv3_genuinely_dead_sid_caught(world):
    sessions = {"combo-proxy": {"session_id": "sid-nowhere",
                                "project_dir": str(world / "-p-a")}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV3"] == 1
    assert "no transcript" in r["findings"]["INV3"][0]["reason"]


def test_inv3_missing_and_wrong_project_dir_caught(world):
    _transcript(world, "-p-real", "sid1", declares="alpha")
    missing = {"alpha": {"session_id": "sid1"}}
    assert _check(missing, {}, world)["findings"]["INV3"][0]["reason"] == \
        "project_dir not recorded"
    wrong = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-WRONG")}}
    assert _check(wrong, {}, world)["findings"]["INV3"][0]["reason"] == \
        "project_dir mismatch"


# ---- INV4 + its two whitelisted exceptions ----------------------------------

def test_inv4_child_session_filed_under_parent_caught(world):
    """ia's real find: assistant-ui-dev's session filed under its PARENT."""
    _transcript(world, "-p-a", "sid1", declares="assistant-ui-dev")
    sessions = {"orchestra-builder-v2": {
        "session_id": "sid1", "project_dir": str(world / "-p-a")},
        "assistant-ui-dev": {}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV4"] == 1
    assert r["findings"]["INV4"][0]["declared"] == "assistant-ui-dev"


def test_inv4_lineage_rename_exempt(world):
    _transcript(world, "-p-a", "sid1", declares="gm-gen13")
    sessions = {"gm": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "gm-gen13": {}}
    registry = {"gm": {"succeeded_by": "gm-gen13"}}
    r = _check(sessions, registry, world)
    assert r["counts"]["INV4"] == 0
    assert any(x["invariant"] == "INV4" for x in r["info"]["lineage_exempt"])


def test_inv4_compaction_summary_MENTION_is_not_a_declaration(world):
    """The false positive ia hit: a summary MENTIONS another agent. A matcher
    that cannot tell a declaration from a mention destroys a correct row."""
    _transcript(world, "-p-a", "sid1", declares="alpha",
                summary_mentions="orchestra-builder")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "orchestra-builder": {}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV4"] == 0, "read a MENTION as a DECLARATION"


# ---- INV5: substance beats recency (ia's shipped fixture) -------------------

def _stub(projects_root, project_dir, sid, agent):
    """The aborted-compaction stub: 3 lines, 3 seconds — and NEWER."""
    d = projects_root / project_dir
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "user", "timestamp": "2026-08-19T01:00:00Z",
         "message": {"content": f"You are {agent}, resuming."}},
        {"type": "assistant", "timestamp": "2026-08-19T01:00:03Z",
         "message": {"content": [{"type": "text", "text": "…"}]}},
    ]
    p = d / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def _head(projects_root, project_dir, sid, agent, lines=710):
    """The real head: many lines, 6 days of span — and OLDER on disk."""
    d = projects_root / project_dir
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "user", "timestamp": "2026-08-13T01:00:00Z",
             "message": {"content": f"You are {agent}, a T2 agent."}}]
    for i in range(lines):
        rows.append({"type": "assistant", "timestamp": "2026-08-19T01:00:00Z",
                     "message": {"content": [{"type": "text",
                                              "text": f"work {i}"}]}})
    p = d / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_inv5_row_pointing_at_a_newer_stub_is_caught(world):
    """client-page-access, verbatim: the 3-line/3-second stub was NEWER than
    the 710-line/6-day head. Modernizing to the newest sid orphans six days
    of work and calls it a repair."""
    head = _head(world, "-p-cpa", "e954c067", "client-page-access")
    stub = _stub(world, "-p-cpa", "3ce2bfb6", "client-page-access")
    os.utime(head, (1, 1))                     # head is OLDER on disk
    assert os.path.getmtime(stub) > os.path.getmtime(head)
    sessions = {"client-page-access": {"session_id": "3ce2bfb6",
                                       "project_dir": str(world / "-p-cpa")}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV5"] == 1
    f = r["findings"]["INV5"][0]
    assert f["row_points_at"] == "3ce2bfb6"
    assert f["substantial_candidates"][0]["session_id"] == "e954c067"


def test_inv5_row_pointing_at_the_head_is_clean(world):
    _head(world, "-p-cpa", "e954c067", "client-page-access")
    _stub(world, "-p-cpa", "3ce2bfb6", "client-page-access")
    sessions = {"client-page-access": {"session_id": "e954c067",
                                       "project_dir": str(world / "-p-cpa")}}
    assert _check(sessions, {}, world)["counts"]["INV5"] == 0


def test_substance_never_consults_mtime(world):
    """The measure must be lines/span. A stub touched to 'now' stays a stub."""
    stub = _stub(world, "-p-a", "s1", "alpha")
    head = _head(world, "-p-a", "h1", "alpha")
    os.utime(head, (1, 1))
    assert S.transcript_substance(str(stub))["is_stub"] is True
    assert S.transcript_substance(str(head))["is_stub"] is False


# ---- INV6: unverifiable is never a pass -------------------------------------

def test_inv6_no_declaration_is_unverifiable_not_passing(world):
    """initiative-architect is the shipped fixture: its session opens with a
    forwarded header, so its own identity checker cannot see its identity."""
    d = world / "-p-ia"
    d.mkdir(parents=True)
    (d / "sidIA.jsonl").write_text(json.dumps({
        "type": "user", "timestamp": "2026-08-19T01:00:00Z",
        "message": {"content": "[fwd from gm] Read /tmp/agent-msg-x.md and "
                               "act on it."}}) + "\n" + json.dumps({
        "type": "assistant", "timestamp": "2026-08-19T01:30:00Z",
        "message": {"content": [{"type": "text", "text": "On it."}]}}) + "\n")
    sessions = {"initiative-architect": {"session_id": "sidIA",
                                         "project_dir": str(d)}}
    r = _check(sessions, {}, world)
    assert r["unverifiable_count"] == 1
    assert r["unverifiable"][0]["agent"] == "initiative-architect"
    assert r["counts"]["INV4"] == 0            # not a violation…
    assert "UNVERIFIABLE" in r["unverifiable"][0]["reason"]   # …but not a pass


def test_inv6_unverifiable_is_reported_separately_from_clean(world):
    d = world / "-p-ia"
    d.mkdir(parents=True)
    (d / "sidIA.jsonl").write_text(json.dumps({
        "type": "user", "message": {"content": "no declaration here"}}) + "\n")
    r = _check({"x": {"session_id": "sidIA", "project_dir": str(d)}}, {}, world)
    # clean means "no violations" — but the unverifiable count must be present
    # and non-zero so a caller can never mistake it for a verified pass
    assert r["clean"] is True and r["unverifiable_count"] == 1


def test_inv4_prose_you_are_the_x_is_not_an_identity(world):
    """'You are the reviewer' must not become an identity claim — the token is
    trusted only when it is a KNOWN agent id."""
    d = world / "-p-a"
    d.mkdir(parents=True)
    (d / "sid1.jsonl").write_text(json.dumps({"type": "user", "message": {
        "content": "You are the gate-integrity reviewer for this change."}}) + "\n")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(d)}}
    r = _check(sessions, {}, world)
    assert r["counts"]["INV4"] == 0            # not a false identity claim…
    # …and per INV6 it is UNVERIFIABLE rather than a silent pass
    assert [u["agent"] for u in r["unverifiable"]] == ["alpha"]


# ---- reporting contract -----------------------------------------------------

def test_reports_never_repairs(world, tmp_path):
    """The tool must not write to the stores it inspects."""
    _transcript(world, "-p-a", "sid1", declares="beta")
    sessions = {"alpha": {"session_id": "sid1", "project_dir": str(world / "-p-a")},
                "beta": {}}
    before = json.dumps(sessions, sort_keys=True)
    _check(sessions, {}, world)
    assert json.dumps(sessions, sort_keys=True) == before


def test_two_character_agent_ids_are_recognizable(world):
    """`gm` is 2 chars. The original quantifier required >=3, so the fleet's
    most important identity was invisible to this predicate on every plane
    that consumes it. Found by a negative fixture, not by review."""
    d = world / "-p-gm"
    d.mkdir(parents=True)
    (d / "gm-sid.jsonl").write_text(json.dumps({
        "type": "user",
        "message": {"content": "You are gm, the general manager."}}) + "\n")
    assert S.declared_identity(str(d / "gm-sid.jsonl"), {"gm"}) == "gm"


def test_short_prose_tokens_still_do_not_become_identities(world):
    """Loosening the length is safe ONLY because a token must be a known
    agent id — 'an' must not become an identity."""
    d = world / "-p-x"
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text(json.dumps({
        "type": "user",
        "message": {"content": "You are an agent working on the pipeline."}}) + "\n")
    assert S.declared_identity(str(d / "s.jsonl"), {"gm", "alpha"}) is None


# ---- INV2 covers WHERE an agent lives, not only WHO it is (P0 2026-08-19) ---

def test_inv2_tmux_divergence_caught_tonights_exact_state(world):
    """The live state that let a recovery daemon spawn a second claude on a
    live sid: registry.gm.tmux='gm' while sessions.gm.tmux='gm-gen14'. The
    sids AGREE, so a sid-only INV2 reported clean while the divergence
    existed and a daemon acted on the stale half."""
    _transcript(world, "-p-gm", "gm-sid", declares="gm")
    sessions = {"gm": {"session_id": "gm-sid", "tmux_session": "gm-gen14",
                       "project_dir": str(world / "-p-gm")}}
    registry = {"gm": {"session_id": "gm-sid", "tmux_session": "gm"}}
    r = _check(sessions, registry, world)
    assert r["counts"]["INV2"] == 1
    f = r["findings"]["INV2"][0]
    assert f["field"] == "tmux_session"
    assert f["sessions"] == "gm-gen14" and f["registry"] == "gm"


def test_inv2_agreeing_tmux_is_clean(world):
    _transcript(world, "-p-gm", "gm-sid", declares="gm")
    sessions = {"gm": {"session_id": "gm-sid", "tmux_session": "gm",
                       "project_dir": str(world / "-p-gm")}}
    registry = {"gm": {"session_id": "gm-sid", "tmux_session": "gm"}}
    assert _check(sessions, registry, world)["counts"]["INV2"] == 0


# ---- INV7: one question, two oracles, and an assertion that they agree -----

def test_inv7_two_oracles_disagreeing_FAILS(monkeypatch):
    """gm's exact state: it updated the registry, verified with the resolver
    that reads the store IT fed, got ('gm', direct-live) and reported
    all-clear — while the other oracle, defaulting to agent-sessions.json,
    returned ('gm-gen14'). Both internally correct. The the operator-facing delivery
    path was routing to the wrong pane the whole time."""
    # ONE resolver, fed each store in turn — resolve_live_head delegates to
    # resolve_delivery_target, so a two-function comparison is vacuous.
    monkeypatch.setattr(S, "_load_resolvers", lambda: {
        "live_head": object(),
        "delivery": lambda a, s, meta: (meta.get(a, {}).get("tmux_session"),
                                        None, "direct-live"),
        "live_tmux": lambda: {"gm", "gm-gen14"}})
    monkeypatch.setattr(S, "tmux_session_exists", lambda n: True)
    out = S.check_resolver_agreement(
        {"gm": {"tmux_session": "gm-gen14"}},              # sessions store
        {"gm": {"status": "online", "tmux_session": "gm"}})  # registry store
    assert len(out) == 1
    assert out[0]["from_sessions"] == "gm-gen14"
    assert out[0]["from_registry"] == "gm"


def test_inv7_agreeing_oracles_pass(monkeypatch):
    monkeypatch.setattr(S, "_load_resolvers", lambda: {
        "live_head": object(),
        "delivery": lambda a, s, meta: (meta.get(a, {}).get("tmux_session"),
                                        None, "direct-live"),
        "live_tmux": lambda: {"gm"}})
    monkeypatch.setattr(S, "tmux_session_exists", lambda n: True)
    assert S.check_resolver_agreement(
        {"gm": {"tmux_session": "gm"}},
        {"gm": {"status": "online", "tmux_session": "gm"}}) == []


def test_inv7_dead_tmux_pointer_is_a_finding(monkeypatch):
    """A pointer naming a session that does not exist: a daemon reading it
    acts on a pane that is not there — or RECREATES it, which is how a
    recovery run put a second writer on a live sid."""
    monkeypatch.setattr(S, "_load_resolvers", lambda: {
        "live_head": object(),
        "delivery": lambda a, s, meta: ("gm", None, "direct-live"),
        "live_tmux": lambda: {"gm"}})
    monkeypatch.setattr(S, "tmux_session_exists", lambda n: n == "gm")
    out = S.check_resolver_agreement(
        {"gm": {"tmux_session": "gm-gen14"}},          # stale pointer
        {"gm": {"status": "online", "tmux_session": "gm"}})
    assert any(f.get("store") == "sessions"
               and f.get("tmux_session") == "gm-gen14" for f in out)


def test_inv7_unavailable_resolvers_is_a_FINDING_not_a_pass(monkeypatch):
    """If the oracles cannot be loaded we have asserted nothing — that must
    never read as agreement (unverifiable is never a pass)."""
    monkeypatch.setattr(S, "_load_resolvers",
                        lambda: {"live_head": None, "delivery": None})
    out = S.check_resolver_agreement({}, {})
    assert len(out) == 1 and "cannot assert agreement" in out[0]["reason"]


def test_no_real_agent_id_is_invisible_to_the_predicate():
    """The sweep gm ordered, as a STANDING test rather than a one-off: every
    id in either live store must be capturable. `gm` (2 chars) and 31
    uppercase-initial ids were invisible; a future id shape that falls
    through this predicate should fail here, not in an incident."""
    import json as _json
    import os as _os
    orch = _os.environ.get("ORCHESTRA_DIR") or _os.path.expanduser("~/orchestra")   # live-fleet check; skips when absent
    try:
        reg = _json.load(open(_os.path.join(orch, "registry.json")))["agents"]
        sess = _json.load(open(_os.path.join(orch, "state",
                                             "agent-sessions.json")))
    except (OSError, ValueError):
        pytest.skip("live stores unavailable")
    invisible = []
    for a in sorted(set(reg) | set(sess)):
        m = S._DECL_RE.fullmatch(f"You are {a}")
        if not m or m.group(1) != a:
            invisible.append(a)
    assert not invisible, f"agent ids invisible to the predicate: {invisible}"


def test_inv7_checks_a_live_agent_whose_registry_status_is_not_online(monkeypatch):
    """`agy`: LIVE pane, registry status 'n/a', registry tmux_session absent
    while sessions carries one. Selecting targets by registry-status alone
    could not see it — and 'the registry does not say where it lives' lands
    in the same place as 'the stores disagree': a resolver that cannot route
    to a live agent."""
    monkeypatch.setattr(S, "_load_resolvers", lambda: {
        "live_head": object(),
        "delivery": lambda a, s, meta: (meta.get(a, {}).get("tmux_session"),
                                        None, "resolved"),
        "live_tmux": lambda: {"agy"}})
    monkeypatch.setattr(S, "tmux_session_exists", lambda n: n == "agy")
    out = S.check_resolver_agreement(
        {"agy": {"tmux_session": "agy"}},          # sessions knows where it is
        {"agy": {"status": "n/a"}})                # registry does not
    div = [f for f in out if "from_sessions" in f]
    assert len(div) == 1
    assert div[0]["from_sessions"] == "agy" and div[0]["from_registry"] is None
