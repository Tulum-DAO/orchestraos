# ptt.py — pure helpers for the Watch push-to-talk endpoint (SPEC_watch-ptt-endpoint.md).
#
# I/O-free so the mechanical parts — request validation, the bounded per-conversation_id history,
# turn_id dedup, and message assembly — are unit-testable without vendors. The STT/brain/TTS calls
# and the lifecycle-free guarantee live in arturo-proxy.ptt_turn (test_ptt_turn.py).
#
# The endpoint is a STATELESS HTTP turn keyed on conversation_id — NOT an ElevenLabs voice call. It
# writes no CallJournal, claims no ended_once, and emits no gm-injection (see arturo-proxy.ptt_turn).

from collections import OrderedDict

# Contract limits (locked with ios-watch-dev msg_ff49325b): .m4a/AAC mono 16k, <=30 s (~60 KB).
MAX_AUDIO_BYTES = 1_000_000      # 1 MB hard cap — a 30 s 16k mono AAC clip is ~60 KB, so this is safe
MAX_DURATION_S = 30
DEFAULT_MAX_HISTORY = 20         # per-conversation turns fed to the brain

# Accept the watch's .m4a/AAC and the octet-stream a multipart field may arrive as. Reject anything
# that clearly isn't audio.
_ALLOWED_CONTENT_TYPES = frozenset({
    "audio/m4a", "audio/x-m4a", "audio/mp4", "audio/aac", "audio/mpeg",
    "application/octet-stream", "",
})


def validate_audio(size, content_type):
    """(ok, error_code). error_code in {'empty','too_large','bad_type'} on failure, else None."""
    if size <= 0:
        return False, "empty"
    if size > MAX_AUDIO_BYTES:
        return False, "too_large"
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in _ALLOWED_CONTENT_TYPES:
        return False, "bad_type"
    return True, None


# Web composer dictation clips (item C): what MediaRecorder produces — webm/opus (Chrome, Firefox),
# mp4/aac (Safari), ogg, plus wav for tools. 10 MB cap: 60 s of opus is ~0.5 MB, so this is safe and
# still shuts out abuse. SEPARATE from the watch rule above on purpose — that contract stays m4a/1 MB.
MAX_TRANSCRIBE_BYTES = 10_000_000
_TRANSCRIBE_CONTENT_TYPES = frozenset({
    "audio/webm", "video/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/wave",
    "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "audio/mpeg", "audio/mp3",
    "application/octet-stream", "",
})


def validate_transcribe_audio(size, content_type):
    """(ok, error_code) for a dictation clip. error_code in {'empty','too_large','bad_type'}."""
    if size <= 0:
        return False, "empty"
    if size > MAX_TRANSCRIBE_BYTES:
        return False, "too_large"
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in _TRANSCRIBE_CONTENT_TYPES:
        return False, "bad_type"
    return True, None


DEFAULT_MAX_CONVERSATIONS = 256   # cap distinct conversation_ids on the long-lived :5071 process


class PttHistory:
    """Bounded, per-conversation_id turn history (system prompt is added at send time, not stored).
    In-memory and process-local. Bounded TWO ways: each conversation keeps only its last max_turns
    turns, AND the number of distinct conversation_ids is LRU-capped at max_conversations so the
    long-lived process can't leak an entry per conversation the watch ever mints (it resets
    conversation_id on new-conversation / relaunch)."""

    def __init__(self, max_turns=DEFAULT_MAX_HISTORY, max_conversations=DEFAULT_MAX_CONVERSATIONS):
        self.max_turns = max_turns
        self.max_conversations = max_conversations
        self._by_conv = OrderedDict()

    def append(self, conversation_id, role, content, brain=None):
        """`brain` = the {provider, model} that WROTE an assistant turn, so a later turn on a
        different model can be told which replies were not its own."""
        turns = self._by_conv.get(conversation_id)
        if turns is None:
            turns = []
            self._by_conv[conversation_id] = turns
        self._by_conv.move_to_end(conversation_id)          # mark most-recently-used
        turn = {"role": role, "content": content}
        if brain:
            turn["brain"] = dict(brain)
        turns.append(turn)
        if len(turns) > self.max_turns:
            del turns[: len(turns) - self.max_turns]         # keep the last max_turns
        while len(self._by_conv) > self.max_conversations:
            self._by_conv.popitem(last=False)                # evict the oldest conversation

    def get(self, conversation_id):
        return list(self._by_conv.get(conversation_id, []))


class TurnCache:
    """turn_id -> result, bounded LRU-ish. Idempotency for tunnel retries: a repeated turn_id returns
    the cached turn instead of re-running STT/brain/TTS."""

    def __init__(self, max_entries=256):
        self.max_entries = max_entries
        self._d = OrderedDict()

    def get(self, turn_id):
        if turn_id in self._d:
            self._d.move_to_end(turn_id)
            return self._d[turn_id]
        return None

    def put(self, turn_id, result):
        self._d[turn_id] = result
        self._d.move_to_end(turn_id)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)             # evict oldest


# What a chat message may carry to a model. Anything else a history turn holds (the brain that
# wrote it) is bookkeeping, and an OpenAI-compatible API rejects keys it does not know.
_CHAT_FIELDS = ("role", "content", "tool_calls", "tool_call_id", "name")


def build_messages(system_context, history, user_text, max_history=DEFAULT_MAX_HISTORY,
                   current_brain=None, label=None):
    """[system(context)] + last max_history history turns + the new user turn.

    Every message is a fresh dict of chat fields only — the history is never mutated. When
    `current_brain` is known, an assistant turn written by a DIFFERENT {provider, model} gets
    `answered_by` (its label), which render_transcript puts on the speaker line: a model that
    reads another model's answer as its own calls it wrong (Codex, told "Claude Opus 5.5" by an
    earlier turn, answered that it was "incorrect for this session", 2026-09-30). A turn with no
    brain on record is left as it is."""
    label = label or brain_label
    trimmed = history[-max_history:] if len(history) > max_history else list(history)
    out = [{"role": "system", "content": system_context}]
    for turn in trimmed:
        msg = {k: turn[k] for k in _CHAT_FIELDS if k in turn}
        wrote = turn.get("brain")
        if (current_brain and wrote and turn.get("role") == "assistant"
                and _same_brain(wrote, current_brain) is False):
            msg["answered_by"] = label(wrote)
        out.append(msg)
    return out + [{"role": "user", "content": user_text}]


def _same_brain(a, b):
    return ((a.get("provider") or ""), (a.get("model") or "")) == \
           ((b.get("provider") or ""), (b.get("model") or ""))


_PRETTY = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "api": "the API model"}


def brain_label(brain):
    """'Claude claude-opus-5-5' — the model is always named, so a switch between two models of
    one provider is as visible as a switch between providers."""
    provider = (brain or {}).get("provider") or ""
    model = (brain or {}).get("model") or ""
    name = _PRETTY.get(provider, provider or "another model")
    return f"{name} {model}" if model else f"{name} (its default model)"
