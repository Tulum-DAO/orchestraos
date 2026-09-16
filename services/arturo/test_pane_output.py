# test_pane_output.py — get_agent_output ghost/draft-stripping (gm msg_91dd9aa5).
# The tool misattributed a CLI GHOST SUGGESTION in gm's composer as gm's actual last
# statement (the operator-caught live, vc_client_573f6b8afffe). Fix is MECHANICAL: capture with
# SGR (-e), classify the composer via the fleet-blessed agent-status style-walk, and
# return only real agent output (content ABOVE the input box), tagging ghost/draft.
from services.arturo import pane_output as po


# --- synthetic raw ANSI panes matching CC screen anatomy (bottom-up):
#     output ... / rule / ❯ composer / rule / (status)
RULE = "────────────────────────────────────────"
ESC = "\033"


def _pane(output_lines, composer_raw):
    return "\n".join(output_lines + [RULE, composer_raw, RULE])


def test_ghost_composer_stripped_from_output():
    # composer holds a DIM (SGR 2) CLI ghost suggestion — not gm's words
    ghost = f"❯ {ESC}[2mGive orchestra-builder something to do, then test leg-3{ESC}[0m"
    raw = _pane(["gm: I've queued the adaptiv review.",
                 "gm: waiting on the vertex harness."], ghost)
    res = po.split_agent_pane(raw)
    # the ghost text must NOT appear in the returned agent output
    assert "orchestra-builder something to do" not in res["output"]
    # real output above the box is preserved
    assert "queued the adaptiv review" in res["output"]
    assert "waiting on the vertex harness" in res["output"]
    # ghost surfaced separately, tagged
    assert "orchestra-builder something to do" in res["ghost"]
    assert res["draft"] == ""


def test_reverse_video_cursor_ghost_is_ghost():
    # cursor (SGR 7) parked at char 0 over a dim body = ghost/placeholder, not typed
    ghost = f"❯ {ESC}[7m{ESC}[2mfix the lint errors in surface.py{ESC}[0m"
    raw = _pane(["agent: done with phase 1."], ghost)
    res = po.split_agent_pane(raw)
    assert "fix the lint errors" not in res["output"]
    assert "fix the lint errors" in res["ghost"]


def test_typed_draft_tagged_not_output_not_ghost():
    # DEFAULT-styled composer text = an unsubmitted DRAFT, not delivered, not a ghost
    draft = "❯ redeploy with the hotfix please"
    raw = _pane(["agent: build is green."], draft)
    res = po.split_agent_pane(raw)
    assert "redeploy with the hotfix" not in res["output"]   # not delivered
    assert "redeploy with the hotfix" in res["draft"]
    assert res["ghost"] == ""
    assert "build is green" in res["output"]


def test_output_is_only_content_above_the_box():
    # anything at/below the input box rule is chrome, never agent output
    raw = _pane(["agent: the answer is 42."],
                f"❯ {ESC}[2msome ghost{ESC}[0m")
    res = po.split_agent_pane(raw)
    assert "the answer is 42" in res["output"]
    assert RULE not in res["output"]           # framing rule stripped
    assert "some ghost" not in res["output"]


def test_no_composer_box_returns_all_as_output():
    # a pane with no input box (pure scrollback) → everything is output, no ghost/draft
    raw = "agent: line one\nagent: line two\nagent: line three"
    res = po.split_agent_pane(raw)
    assert "line one" in res["output"] and "line three" in res["output"]
    assert res["ghost"] == "" and res["draft"] == ""


def test_render_for_voice_omits_ghost_and_tags_draft():
    # the spoken/returned rendering must never present a ghost as agent output;
    # a draft is tagged as not-delivered so Arturo can say so honestly
    ghost = f"❯ {ESC}[2mghost suggestion text here{ESC}[0m"
    raw = _pane(["gm: shipping the fix now."], ghost)
    rendered = po.render_for_voice(po.split_agent_pane(raw))
    assert "shipping the fix now" in rendered
    assert "ghost suggestion text here" not in rendered

    draft = "❯ my unsent words"
    rendered2 = po.render_for_voice(po.split_agent_pane(_pane(["gm: ok."], draft)))
    assert "my unsent words" not in rendered2 or "not delivered" in rendered2.lower() \
        or "draft" in rendered2.lower()


def test_agent_status_module_is_memoized():
    # the ~500-line agent-status.py must be exec'd at most ONCE across calls
    # (voice hot path) — the loader caches the module (reviewer DEC-1787087455).
    po._AGENT_STATUS = None                       # reset cache for a clean measure
    calls = {"n": 0}
    real_loader = po._load_agent_status.__wrapped__ if hasattr(
        po._load_agent_status, "__wrapped__") else None
    m1 = po._agent_status_mod()
    m2 = po._agent_status_mod()
    assert m1 is m2                               # same cached object, not re-exec'd
