"""CLAIM GATE (gm msg_0aec5c71, design msg_b4cb2363): Arturo must not SAY he started / sent / saved / did something
unless a tool call actually did it.

In native mode GPT-Live speaks directly, so a prompt rule alone cannot make that true. MEASURED on test drive 3
(trace poc-conv-67ddc51ef988, 2026-10-15): on all 11 utterances the output TRANSCRIPT arrived before the audio
(typically ~0.2 s ahead). So the server holds Arturo's audio HOLD_S behind his words; when the words of the current
utterance make a past/progress claim of an action and no side-effecting tool succeeded this turn, the rest of that
utterance's audio is dropped (never reaches the phone) and one correction line is owed.

Passes (by design): capability ("I can start one, what should it do?"), future/read-back ("I'll start a seat called
research-bot to ..."; the spawn read-back rule REQUIRES that), questions, and any claim backed by a receipt.
"""
import collections
import re

HOLD_S = float(__import__("os").environ.get("ARTURO_NATIVE_CLAIM_HOLD_S", "0.3"))
CORRECTION = "Sorry, I haven't actually done that yet."      # Shaw 10-16: never ask permission again after a yes

_SUBJ = r"(?:i(?:'ve| have)?|i just|we(?:'ve| have)?)"
_ADV = r"(?:already\s+|now\s+|just\s+|also\s+)?"
# ACTION claims, by class: a receipt backs a claim only if a tool of THAT class succeeded this turn (review M6: "I sent
# the text" must not be backed by an unrelated spawn). "stop" has no voice tool at all (NO KILLS), so it is never backed.
_CLASSES = {
    "start": r"(?:started|spawned|created|launched|kicked off|set up|spun up|got (?:it|that|one) (?:going|started|running))",
    "message": r"(?:sent|messaged|told(?!\s+you)|asked(?!\s+you)|pinged|texted|emailed|forwarded|submitted|passed (?:it|that) on)",
    "save": r"(?:saved|noted|written (?:it |that )?down|wrote (?:it |that )?down|added (?:it|that)|stored)",
    "stop": r"(?:stopped|paused|restarted|killed|cancell?ed|shut (?:it |that )?down)",
    "fix": r"(?:fixed|updated|deployed|scheduled|queued|done (?:it|that)|did (?:it|that)|took care of (?:it|that)|"
           r"went ahead and \w+)",
}
_ACTION_RES = [(cls, re.compile(rf"\b{_SUBJ}\s+{_ADV}(?:got (?:it|that) )?{verb}", re.I)) for cls, verb in _CLASSES.items()]
# "I've got it started" / subjectless "Started." "Sent." "Saved." at the start of a sentence
_ACTION_RES += [
    ("start", re.compile(r"(?:^|[.!?]\s+)(?:started|spawned|launched)[.!,]", re.I)),
    ("message", re.compile(r"(?:^|[.!?]\s+)sent[.!,]", re.I)),
    ("save", re.compile(r"(?:^|[.!?]\s+)(?:saved|noted)[.!,]", re.I)),
]
# STATE claims: something is done / running / saved. Backed by ANY successful tool call this turn (a status read is
# evidence: "the build is finished" after get_agent_output said so). Present / present-perfect only: "it was created
# yesterday" is history, not a claim (gm msg_04c7d9bd).
_STATE_RES = [
    re.compile(r"\b(?:it|that|this|the (?:task|seat|agent|note|message))(?:'s| is| has been)\s+(?:now\s+)?"
               r"(?P<word>done|saved|started|sent|running|set up|created|launched|queued|noted|taken care of|handled|"
               r"on its way|under ?way|up|live)\b", re.I),
    re.compile(r"\b(?:all )?(?:done|finished)[.!]|\btaken care of\b|\bconsider it done\b", re.I),
]
# NOT claims, by design: progress/announcement tense ("I'm starting a seat called X to ..." is how the spawn read-back
# is said BEFORE the call), the future ("I'll start ..."), capability ("I can start one"), questions, other people's
# actions ("you started"), and narration to Shaw ("I told you", "I asked you").


# PROGRESS state claims ("that's running now", "it's under way") say work is going, not that it is done: a tool still
# running after its "accepted" receipt backs these (Shaw 2026-11-27: "that's running now" and other phrases besides
# "I've started it"); every other state claim needs a finished result.
# "started" is NOT progress: "the agent has been started" claims a finished start (start-class evidence, codex on #698).
_PROGRESS = re.compile(r"^(?:running|queued|on its way|under ?way)$", re.I)
# ...and an utterance with ANY completion word anywhere is never progress, however it is joined ("it's running and
# saved", "under way; that's already saved", "the message was delivered": codex rounds 2-3 on #698). Conservative on
# purpose: it then needs a result. Only the modal FUTURE is exempt ("it'll be done in a minute", "should be saved by
# then") and the ongoing "being saved"; "confirmed to be saved" is a completion claim.
_COMPLETION = re.compile(r"\b(?:done|finished|complete[d]?|saved|sent|started|created|launched|noted|stored|added|"
                         r"handled|fixed|updated|set up|taken care of|delivered|confirmed|posted|submitted|recorded|"
                         r"logged|scheduled|deployed|booked|paid|messaged|texted|emailed|forwarded)\b", re.I)
_FUTURE_BEFORE = re.compile(r"(?:(?:'ll|\bwill|\bshould|\bgoing to|\bgonna)\s+be\s+(?:\w+\s+)?|\bbeing\s+)$", re.I)


# A blocklist of completion words always misses one ("your note has been written down": codex round 4 on #698), so
# beside it any past form counts too: a word ending in -ed or a common irregular participle, other than the progress
# words themselves. Conservative on purpose: "I just checked, it's running" then needs a result as well.
_PAST = re.compile(r"\b(?:\w{2,}(?<!e)ed|written|wrote|done|did|sent|made|set|put|given|gave|taken|took|built|kept|found|"
                   r"got|gotten|shown|showed|told|paid|bought|brought|left|begun|began|run|ran|went|gone|broadcast|"
                   r"read|cut|let|shut|spread|sold|held|won|met|meant|fed|led|lost|hung|stuck|struck|drew|drawn|"
                   r"threw|thrown|knew|known|grew|grown|flew|flown|wore|worn|tore|torn|froze|frozen|spoke|spoken|"
                   r"broke|broken|chose|chosen|woke|woken|rode|ridden|rose|risen|drove|driven|ate|eaten|fell|fallen)\b",
                   re.I)
_NOT_PAST = frozenset({"hundred", "red", "bed", "shed", "sacred", "naked", "wicked", "kindred", "rugged", "jagged",
                       "ragged", "crooked", "beloved", "read"})     # -ed words that are no past ("need": (?<!e)ed)
# THE CRITERION (#698): a progress phrase admits only itself. Every other clause of the utterance is judged at least as
# strictly as main judges that clause on its own (the action and state recognisers run on the whole text, and this
# past-form rule adds more). A completion phrased in a way main never recognised ("I wrote the note." alone passes on
# main) is a limit of the base gate, not of this path.


def _completes(text):
    for rx in (_COMPLETION, _PAST):
        for m in rx.finditer(text or ""):
            if (_PROGRESS.match(m.group(0)) or m.group(0).lower() in _NOT_PAST
                    or _FUTURE_BEFORE.search(text[max(0, m.start() - 32):m.start()])):
                continue
            return True
    return False


def claim_kinds(text):
    """EVERY claim in the utterance, each of which must be backed on its own (codex round 3 on #698: "I've started it
    and it's done" was checked only as a start). Kinds: ("action", class), ("state", "progress"), ("state", None)."""
    text = text or ""
    kinds = list(dict.fromkeys(("action", cls) for cls, r in _ACTION_RES if r.search(text)))
    words = [m.group("word") for m in _STATE_RES[0].finditer(text)]
    other = any(r.search(text) for r in _STATE_RES[1:])
    rest = text                                              # an action claim's own verb is checked as that action
    for _cls, r in _ACTION_RES:
        rest = r.sub(lambda m: " " * len(m.group(0)), rest)
    if words and not other and all(_PROGRESS.match(w) for w in words) and not _completes(rest):
        kinds.append(("state", "progress"))                 # every state claim in it is progress, nothing completes
    elif words or other:
        kinds.append(("state", None))
    return kinds


def claim_kind(text):
    """The first claim in the utterance (claim_kinds has them all): ("action", class) | ("state", ...) | None."""
    ks = claim_kinds(text)
    return ks[0] if ks else None


def is_claim(text):
    return claim_kind(text) is not None


class ClaimGate:
    """Per call. feed_transcript() with Arturo's output transcript deltas, feed_audio() with his audio frames,
    release(now) for frames due to play; end_utterance() when his speech span ends. is_backed() -> bool says whether a
    the claim is backed: is_backed(("action", cls)) = a tool of that class succeeded this turn, is_backed(("state", None))
    = any tool succeeded this turn (the server asks the ledger). LATE (gm msg_04c7d9bd): audio that arrives before any of
    its words is never stalled: it plays after the hold (fail open) and the utterance is counted in `late`."""

    def __init__(self, is_backed, hold_s=HOLD_S, enabled=True):
        self.is_backed, self.hold_s, self.enabled = is_backed, hold_s, enabled
        self._q = collections.deque()          # (arrived_at, pcm)
        self.text = ""
        self.blocked = False
        self.blocked_text = None
        self.dropped_s = 0.0
        self.blocks = []                        # utterances blocked this call (for the report / log)
        self.correction_owed = False
        self.late = 0                           # utterances whose audio played before any of their words
        self._released_wordless = False
        self._late_counted = False

    def feed_transcript(self, delta):
        self.text += delta or ""
        if self._released_wordless and not self._late_counted and self.text.strip():
            self.late += 1
            self._late_counted = True
        kinds = claim_kinds(self.text) if self.enabled and not self.blocked else []
        if any(not self.is_backed(k) for k in kinds):        # every claim in it needs its own backing
            self.blocked, self.blocked_text = True, self.text.strip()
            self.blocks.append(self.blocked_text[-200:])
            self.correction_owed = True
            self._drop_queued()

    def feed_audio(self, pcm, now):
        if self.blocked:
            self.dropped_s += len(pcm) / 32000.0
            return
        self._q.append((now, pcm))

    def release(self, now, flush=False):
        """Frames whose hold has passed (all of them with flush=True: span end, or the gate is off)."""
        out = []
        if self.blocked:
            self._drop_queued()
            return out
        while self._q and (flush or not self.enabled or now - self._q[0][0] >= self.hold_s):
            out.append(self._q.popleft()[1])
        if out and not self.text.strip():
            self._released_wordless = True
        return out

    def end_utterance(self, now):
        """Arturo's span ended: release what is held (unless blocked), start a fresh utterance. Returns frames."""
        out = self.release(now, flush=True)
        self.text, self.blocked, self.blocked_text = "", False, None
        self._released_wordless = self._late_counted = False
        return out

    def take_correction(self):
        owed, self.correction_owed = self.correction_owed, False
        return CORRECTION if owed else None

    def barge(self):
        """Shaw cut in: whatever is held is never played."""
        self._drop_queued()
        self.text, self.blocked, self.blocked_text = "", False, None
        self._released_wordless = self._late_counted = False

    def _drop_queued(self):
        while self._q:
            self.dropped_s += len(self._q.popleft()[1]) / 32000.0
