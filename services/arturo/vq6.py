# vq6.py — supersede-on-arrival for pause-duplication (Arturo voice).
# Congruence: DEC-1786425204 CONSENSUS_REACHED (jarvis-v4-agy + jarvis-v4-claude-reviewer APPROVE).
#
# When the operator pauses mid-utterance ElevenLabs' endpointer finalizes a PARTIAL utterance and POSTs it
# (request A); the FULL utterance then arrives as a second POST (request B). Both get answered ->
# a duplicate response. This module lets B cancel A's in-flight TEXT response, but ONLY while A has
# not yet committed a side-effecting tool dispatch (cancel cannot un-run execute_tool). The
# commit/cancel decision is a single atomic step under one lock, so there is no double-dispatch and
# no TOCTOU. See .workspace/proposals/vq6-supersede-on-arrival-spec-v3.md.
import threading
import difflib

from services.arturo.call_journal import _norm_fuzzy


def is_partial_of(partial, full):
    """True if `full` supersedes `partial` — either `partial` was a premature PREFIX and `full` is
    its completion (full strictly longer), OR the two are the SAME utterance RE-TRANSCRIBED with
    tiny variation (BUG-3: app-dev-v4 found ElevenLabs re-emits an utterance same-length with a
    punctuation/one-word swap — "...from. And" vs "...from, and", ratio 1.0 — which the old
    strictly-longer guard missed). Reuses the ebc3c66 fuzzy normalization. Stays directional-safe:
    the same-length branch demands a VERY high ratio (0.94) + <=2 differing tokens so two DISTINCT
    turns never cancel each other."""
    tp, tf = _norm_fuzzy(partial), _norm_fuzzy(full)
    if not tp or not tf:
        return False
    if len(tf) > len(tp):
        if tf[:len(tp)] == tp:
            return True                    # partial is a clean token-prefix of full
        ratio = difflib.SequenceMatcher(None, " ".join(tp), " ".join(tf)).ratio()
        return ratio >= 0.9                 # near-identical superset (full still longer)
    # SAME-LENGTH (or shorter) re-transcription: only supersede on a very high similarity + tiny
    # token diff, so a genuinely different utterance of equal length is never falsely cancelled.
    if abs(len(tf) - len(tp)) <= 1 and len(set(tp) ^ set(tf)) <= 2:
        ratio = difflib.SequenceMatcher(None, " ".join(tp), " ".join(tf)).ratio()
        return ratio >= 0.94
    return False


def fuzzy_repeat(recent_texts, thresh=0.9):
    """Fuzzy upgrade of the exact `len(set)==1` assistant-loop guard: True when the 3 most recent
    assistant texts are near-identical ("Still on it." vs "Still on it. ") — catches similar-not-
    identical repeats the exact check misses."""
    if len(recent_texts) < 3:
        return False
    a = recent_texts[0]
    return all(
        difflib.SequenceMatcher(None, a, b).ratio() >= thresh
        for b in recent_texts[1:3]
    )


def new_entry(turn):
    """One in-flight voice generation's registry entry."""
    return {
        "turn": turn,
        "cancel": threading.Event(),
        "tool_dispatched": False,
        "cancelled": False,
    }


class InflightRegistry:
    """Tracks the currently in-flight voice generation per call key. Lock is held ONLY for tiny
    map/flag reads/writes + Event.set() — NEVER across execute_tool / model calls / _JOURNALS_LOCK
    / any IO — so the ordering stays flat and deadlock-free."""

    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def register_and_supersede(self, key, entry, this_turn):
        """Register `entry` under `key`. If a PRIOR entry exists that (a) is not `entry`, (b) has
        NOT latched a tool dispatch, and (c) whose turn `is_partial_of` `this_turn` — cancel it (it
        answered a premature partial). Register+supersede happen under ONE lock hold (atomic vs
        commit_or_abort). Returns the superseded prior entry (or None) — for tests/logging."""
        superseded = None
        with self._lock:
            prior = self._d.get(key)
            if (prior is not None and prior is not entry
                    and not prior["tool_dispatched"]
                    and is_partial_of(prior["turn"], this_turn)):
                prior["cancelled"] = True
                prior["cancel"].set()
                superseded = prior
            self._d[key] = entry
        return superseded

    def commit_or_abort(self, entry):
        """Atomic gate placed immediately BEFORE every side-effecting execute_tool on the voice
        path. Returns True if the caller must ABORT (a superseding request already cancelled us —
        do NOT dispatch, suppress output); else latches `tool_dispatched=True` (after which this
        entry can no longer be cancelled) and returns False."""
        with self._lock:
            if entry["cancel"].is_set():
                entry["cancelled"] = True
                return True
            entry["tool_dispatched"] = True
            return False

    def cleanup(self, key, entry):
        """Identity-gated slot removal in the owner's finally: only delete the map slot if THIS
        entry still owns it (a later request B may have overwritten it — never clobber B)."""
        with self._lock:
            if self._d.get(key) is entry:
                del self._d[key]

    def current(self, key):
        """Test/diagnostic peek."""
        with self._lock:
            return self._d.get(key)
