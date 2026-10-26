"""An unreadable brain reply is asked again once, not ended on "I garbled that one" (2026-10-24)."""
from services.arturo import brain as b

TOOLS = [{"type": "function", "function": {"name": "run_command", "parameters": {"type": "object", "properties": {}}}}]


def _brain(replies):
    seen = []

    def runner(argv, prompt, timeout=None, **k):
        seen.append(prompt)
        return replies.pop(0)
    rb = b.RuntimeBrain("claude", "claude", runner=runner)
    rb._text = lambda messages, tools, tool_choice, timeout: (seen.append(messages), replies.pop(0))[1]
    return rb, seen


def test_an_unreadable_reply_is_asked_again_once():
    rb, seen = _brain(['<invoke name="">broken', "Here is what I found."])
    out = rb.complete([{"role": "user", "content": "how much spend is left"}], tools=TOOLS)
    assert out.choices[0].message.content == "Here is what I found."
    assert seen[1][-1]["content"] == b.UNREADABLE_RETRY


def test_it_is_asked_again_only_once():
    rb, seen = _brain(['<invoke name="">broken', '<invoke name="">still broken'])
    out = rb.complete([{"role": "user", "content": "x"}], tools=TOOLS)
    assert out.choices[0].message.content == b.NATIVE_MARKUP_FALLBACK and len(seen) == 2


def test_the_log_line_never_carries_a_key():
    line = b.redact("curl -H 'Authorization: Bearer sk-proj-abcdef123456789' ; OPENAI_API_KEY=sk-zzz999 ok")
    assert "123456789" not in line and "zzz999" not in line and "Bearer sk-[redacted]" in line
