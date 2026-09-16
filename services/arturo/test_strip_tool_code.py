# test_strip_tool_code.py — a tool_code block in the model reply must NOT reach journal text / TTS.
# (build vc_client_981fecb4fcbf: the operator saw "tool code print def..." rendered in the Arturo conversation.)
from services.arturo import voice_guards as vg


def test_strips_fenced_tool_code_block():
    reply = ("Sure the operator, here's the update.\n\n"
             "```tool_code\nprint(default_api.remember_note(note='x', category='fact'))\n```\n\n"
             "Everything's green.")
    out = vg.strip_tool_code(reply)
    assert "tool_code" not in out
    assert "default_api" not in out
    assert "print(" not in out
    assert "here's the update" in out and "Everything's green" in out


def test_strips_bare_tool_code_and_default_api_lines():
    reply = "On it.\ntool_code\nprint(default_api.get_agent_output(session_name='x'))\nDone."
    out = vg.strip_tool_code(reply)
    assert "tool_code" not in out and "default_api" not in out
    assert "On it." in out and "Done." in out


def test_preserves_plain_speech():
    reply = "Hey the operator, three agents are online and the deploy is green."
    assert vg.strip_tool_code(reply) == reply


def test_preserves_a_legit_non_tool_code_fence():
    # a normal (non-tool) fenced block should survive — don't over-strip.
    reply = "Here's the snippet you asked for:\n```\nrsync -av a b\n```\nThat's it."
    out = vg.strip_tool_code(reply)
    assert "rsync -av a b" in out


def test_empty_and_none_safe():
    assert vg.strip_tool_code("") == ""
    assert vg.strip_tool_code(None) is None
