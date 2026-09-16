# keepalive.py — narration etiquette (spec §0.4) + announcement queue.
# Bands per docs/voice-silence-probe-findings.md: inline (≤5s) + async (>30s) are PROVEN;
# narrated (5–30s) + silent keepalive stay gated on the live probe (probe_silent_ok).


def band(est_s):
    if est_s <= 5:
        return "inline"
    if est_s <= 30:
        return "narrated"
    return "async"


class Narration:
    def __init__(self, mode="narrate", probe_silent_ok=False):
        self.mode = mode                 # "narrate" | "quiet"
        self.probe_silent_ok = probe_silent_ok

    def plan(self, estimate_s):
        b = band(estimate_s)
        if b == "inline":
            return "inline"
        if self.mode == "quiet" and not self.probe_silent_ok:
            return "async"               # §4: no silent keepalive proof → long ops go async
        return "async" if b == "async" else "narrated"


class AnnouncementQueue:
    def __init__(self):
        self._progress = []              # timestamped narration morsels (discardable)
        self._pending = []               # completed-work announcements awaiting a gap

    def note_progress(self, morsel):
        self._progress.append(morsel)

    def pending_progress(self):
        return list(self._progress)

    def on_complete(self, summary):
        self._progress.clear()           # BACKLOG DISCARD on completion (§0.4)
        self._pending.append(summary)

    def has_pending(self):
        return bool(self._pending)

    def next_announcement(self):
        if not self._pending:
            return None
        summary = self._pending.pop(0)
        return (f"By the way — {summary} Want to hear what happened, "
                f"or keep going and ask me later?")
