"""RED-first tests for continue_capsule.py — BG Layer-3 working-state continue-capsule.

Congruence DEC-1788658573 (rotation-autonomy-builder-claude + -agy APPROVE). The re-run gap:
a freshly-booted green latched an OLD decision-dense *salient* thread instead of blue's CURRENT
objective, because the hydrate digest ships a decision TIMELINE the green must *infer* from. The
capsule STATES {objective, current_step, next_action, artifact_refs}, derived deterministically
(NO LLM — contagion firewall) from blue's CURRENT working-state.

Grounding finding (live WAL): file_mod/git events are captured scoped to blue's CWD repo ONLY, so
they are BLIND to a cross-repo objective (blue cwd=second-brain, work=agent-orchestra) — which is
exactly why the green latched the local orb thread. So the OBJECTIVE is anchored on the most-recent
NON-NOISE `prompt` directive thread (world-INPUT, scrub-gated), NOT file_mod/marker. AGY mitigations
(mandatory): court_scrub, blockquote/quoted-assistant stripping, origin allowlist (drop stop-hook /
queue-digest / lineage-ping noise), inert encapsulation, bounded N<=3 window. file_mod/git populate
artifact_refs (repo-root disambiguated), never the objective anchor.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import continue_capsule  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

UNKNOWN = "UNKNOWN (derived from recent file mutations)"


def _store(tmp_path, root="bg-drill-victim"):
    return WalStore(str(tmp_path / f"{root}.db"))


def _resolver(mapping):
    """Injected resolve_body seam: body_ref -> raw text (the production reader reads
    path:offset; tests inject a dict). Missing ref -> None (unresolvable)."""
    return lambda ref: mapping.get(ref)


def _prompt(store, root, seq_hint, body_ref, ts):
    store.append(ts=ts, lineage_root=root, generation=4, sid="b", runtime="claude",
                 kind="prompt", summary=f"text ({seq_hint} chars)", body_ref=body_ref,
                 source_path="t.jsonl")


def _filemod(store, root, path_summary, body_ref, ts):
    store.append(ts=ts, lineage_root=root, generation=4, sid="b", runtime="claude",
                 kind="file_mod", summary=path_summary, body_ref=body_ref, source_path="x")


# ── DECISION A/B: objective anchored on the recent directive thread, not the salient old thread ──

def test_objective_is_recent_directive_not_salient_old_thread(tmp_path):
    """The re-run failure, reproduced. An OLD decision-dense thread (orb UI, objective Y) precedes
    the CURRENT directive (objective X). The capsule objective must be X, NEVER Y."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    # OLD salient thread: many events about the orb UI (objective Y), decision-dense.
    for i in range(20):
        store.append(ts=100 + i, lineage_root=root, generation=4, sid="b", runtime="claude",
                     kind="response", summary=f"orb/lobe status-glow design decision {i}",
                     source_path="x")
    _prompt(store, root, 300, "t.jsonl:1000", ts=90)  # old directive (orb)
    # CURRENT directive thread (objective X): AgentEvent stage 3.
    _prompt(store, root, 358, "t.jsonl:9000", ts=500)
    _prompt(store, root, 21, "t.jsonl:9100", ts=600)
    bodies = {"t.jsonl:1000": "build the orb lobe status-glow UI in public/index.html",
              "t.jsonl:9000": "Work AgentEvent stage 3: the snapshot barrier + W5 multipart gate.",
              "t.jsonl:9100": "continue with stage 3"}
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies))
    assert "stage 3" in cap["objective"].lower()
    assert "orb" not in cap["objective"].lower()
    assert cap["degraded"] is False
    assert cap["source"] == "directive-thread"


def test_directive_window_is_bounded_to_three(tmp_path):
    """AGY mitigation: bounded window — at most the last 3 non-noise directives are resolved."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    bodies = {}
    for i in range(6):
        ref = f"t.jsonl:{i}"
        bodies[ref] = f"directive number {i}"
        _prompt(store, root, 20, ref, ts=100 + i)
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies),
                                                  prompt_n=3)
    assert len(cap["directives"]) == 3
    # the three most recent (3,4,5), never the old ones (0,1,2)
    seqs_text = " ".join(d["text"] for d in cap["directives"])
    assert "number 5" in seqs_text and "number 0" not in seqs_text


# ── AGY mitigation: origin allowlist (drop reflection/relay noise prompts) ──

def test_noise_prompts_are_skipped_for_objective(tmp_path):
    """Stop-hook / queue-digest / lineage-ping prompts are relay noise, NOT directives. The
    objective must skip past them to the last real human/gm directive."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 300, "t.jsonl:real", ts=100)     # the real directive
    _prompt(store, root, 200, "t.jsonl:noise1", ts=200)   # noise (more recent)
    _prompt(store, root, 200, "t.jsonl:noise2", ts=300)   # noise (most recent)
    bodies = {
        "t.jsonl:real": "Work AgentEvent stage 3 in the agent-orchestra repo.",
        "t.jsonl:noise1": "Stop hook feedback:\n[QUEUE-DIGEST] 1 pending message(s) older than 30s",
        "t.jsonl:noise2": "[MSG from lineage-daemon | high] [LINEAGE SOFT-HANDOFF] author your successor",
    }
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies))
    assert "stage 3" in cap["objective"].lower()
    assert "queue-digest" not in cap["objective"].lower()
    assert "soft-handoff" not in cap["objective"].lower()


# ── AGY mitigation: quote sanitization (strip embedded model-voice) ──

def test_blockquoted_model_voice_is_stripped_from_directive(tmp_path):
    """A correction prompt can embed prior model-voice as a markdown blockquote — the primary
    contagion path. Blockquote lines must be stripped before the objective is assembled."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 300, "t.jsonl:q", ts=100)
    bodies = {"t.jsonl:q": ("Fix the parser. Do not repeat this:\n"
                            "> COURTGLITCH ornate incantation model-voice\n"
                            "just resume stage 3.")}
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies))
    assert "courtglitch" not in cap["objective"].lower()
    assert "stage 3" in cap["objective"].lower()


def test_court_scrub_excludes_poisoned_directive(tmp_path):
    """A poisoned prompt body tripping court_scrub is hard-excluded (bytes-only), never surfaced
    in the objective — same firewall boundary the digest uses for tool_result."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 300, "t.jsonl:poison", ts=100)
    _prompt(store, root, 300, "t.jsonl:clean", ts=90)
    bodies = {"t.jsonl:poison": "SIGWORD_XYZ contaminated span", "t.jsonl:clean": "resume stage 3"}

    def scrub(text):
        if "SIGWORD_XYZ" in text:
            return "", True  # (clean_text, contaminated)
        return text, False

    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies),
                                                  scrub=scrub)
    assert "sigword_xyz" not in cap["objective"].lower()


# ── AGY mitigation: active-vs-passive partitioning (exploratory tail) ──

def test_exploratory_read_tail_does_not_become_current_step(tmp_path):
    """The last event is a passive grep in second-brain, AFTER a real file_mod in agent-orchestra.
    current_step/next_action must bind to the active work, not the read-only probe."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 30, "t.jsonl:d", ts=50)
    _filemod(store, root, " M projection.py",
             "git:/home/testuser/agent-orchestra#scripts/lineage_daemon/wal/projection.py", ts=100)
    # passive read-only tail (grep), most recent
    store.append(ts=200, lineage_root=root, generation=4, sid="b", runtime="claude",
                 kind="tool_call", summary="Grep pattern in second-brain/public", source_path="x")
    bodies = {"t.jsonl:d": "AgentEvent stage 3 projection work"}
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies))
    assert "grep" not in (cap["current_step"] or "").lower()
    assert "projection.py" in (cap["current_step"] or "") or "projection.py" in cap["next_action"]


# ── AGY mitigation: repo-root disambiguation (cross-repo cwd drift) ──

def test_artifact_refs_resolve_canonical_repo_root_across_cwd(tmp_path):
    """file_mods land in agent-orchestra while blue cwd=second-brain. artifact_refs must resolve
    each file_mod's OWN repo root from its body_ref, never default to blue's base cwd."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 30, "t.jsonl:d", ts=50)
    _filemod(store, root, " M projection.py",
             "git:/home/testuser/agent-orchestra#scripts/lineage_daemon/wal/projection.py", ts=100)
    _filemod(store, root, " M index.html",
             "git:/home/testuser/repos/second-brain#public/index.html", ts=110)
    bodies = {"t.jsonl:d": "stage 3"}
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies))
    repos = {a["repo"] for a in cap["artifact_refs"]}
    assert "agent-orchestra" in repos
    paths = {a["path"] for a in cap["artifact_refs"]}
    assert "scripts/lineage_daemon/wal/projection.py" in paths


# ── AGY mitigation: degraded-state contract ──

def test_degraded_when_no_directive_marker(tmp_path):
    """No non-noise directive at all -> objective is the explicit UNKNOWN sentinel (never a
    synthesized string), and artifact_refs still populate from recent file mutations."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 200, "t.jsonl:noise", ts=100)  # only noise
    _filemod(store, root, " M projection.py",
             "git:/home/testuser/agent-orchestra#scripts/lineage_daemon/wal/projection.py", ts=110)
    bodies = {"t.jsonl:noise": "[QUEUE-DIGEST] 1 pending message(s) older than 30s"}
    cap = continue_capsule.build_continue_capsule(store, root, resolve_body=_resolver(bodies))
    assert cap["objective"] == UNKNOWN
    assert cap["degraded"] is True
    assert cap["source"] == "degraded-file-mutations"
    assert any(a["path"].endswith("projection.py") for a in cap["artifact_refs"])


# ── A3: blue-authored checkpoint override (highest priority) ──

def test_blue_authored_checkpoint_overrides_directive_thread(tmp_path):
    """When blue authors an explicit checkpoint, it is the authoritative objective — no inference."""
    root = "bg-drill-victim"
    store = _store(tmp_path)
    _prompt(store, root, 30, "t.jsonl:d", ts=50)
    bodies = {"t.jsonl:d": "some directive"}
    cap = continue_capsule.build_continue_capsule(
        store, root, resolve_body=_resolver(bodies),
        checkpoint={"objective": "AgentEvent stage 3: snapshot barrier",
                    "next_action": "author test_snapshot_barrier.py RED"})
    assert cap["objective"] == "AgentEvent stage 3: snapshot barrier"
    assert cap["source"] == "checkpoint"
    assert cap["degraded"] is False


# ── production resolver: read a prompt body from its transcript body_ref (path:offset), fail-safe ──

def test_resolve_prompt_body_reads_directive_at_offset(tmp_path):
    import json
    tp = tmp_path / "t.jsonl"
    rows = [json.dumps({"type": "user", "message": {"content": "earlier line"}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "text",
                                                                  "text": "continue with stage 3"}]}})]
    data = ("\n".join(rows) + "\n").encode()
    tp.write_bytes(data)
    off = len(rows[0].encode()) + 1  # byte offset of the 2nd line
    got = continue_capsule.resolve_prompt_body(f"{tp}:{off}")
    assert got == "continue with stage 3"


def test_resolve_prompt_body_is_failsafe_on_bad_ref(tmp_path):
    assert continue_capsule.resolve_prompt_body("/nonexistent/file.jsonl:0") is None
    assert continue_capsule.resolve_prompt_body(None) is None
    assert continue_capsule.resolve_prompt_body("git:/repo#path") is None  # not a transcript ref


# ── banner: the capsule rendered for the top of the hydrate body ──

def test_banner_states_objective_and_next_action_prominently(tmp_path):
    cap = {"objective": "AgentEvent stage 3", "current_step": "file_mod  M projection.py",
           "next_action": "Resume blue's CURRENT objective: AgentEvent stage 3",
           "artifact_refs": [{"repo": "agent-orchestra", "path": "scripts/x.py", "body_ref": "git:..#x"}],
           "directives": [{"seq": 9, "text": "continue with stage 3"}],
           "source": "directive-thread", "degraded": False}
    banner = continue_capsule.render_capsule_banner(cap)
    assert "RESUME" in banner.upper()
    assert "AgentEvent stage 3" in banner
    assert "scripts/x.py" in banner
    # inert structural encapsulation: directive rendered in a container, not an open turn
    assert "[DIRECTIVE" in banner
