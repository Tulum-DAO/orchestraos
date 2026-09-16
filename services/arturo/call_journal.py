# call_journal.py — per-call transcript journal (§2 shape, atomic writes).
import json, os, time, secrets, tempfile
from pathlib import Path


def _atomic_write(path: Path, obj) -> None:
    # Canonical pattern (scripts/state-event-hook.py): tmp in the SAME dir + os.replace.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, str(path))


import re as _re

_FILLER = {"uh", "um", "er", "ah", "like", "you", "know", "i", "mean", "just", "so"}


def _norm_user(t):
    return (t if isinstance(t, str) else str(t)).strip()[:200]


def _norm_fuzzy(t):
    # Normalize an utterance for FUZZY matching across ASR re-transcriptions: lowercase, strip
    # punctuation, drop filler words, collapse whitespace. ("...changed your name." and
    # "...changed your name. Or I mean, your, your voice." must be recognized as the SAME call.)
    t = (t if isinstance(t, str) else str(t)).lower()
    t = _re.sub(r"[^a-z0-9\s]", " ", t)
    toks = [w for w in t.split() if w not in _FILLER]
    return toks


def _fuzzy_same_utterance(a, b):
    # Two utterances are "the same" across re-transcription if their normalized token lists share
    # a long common PREFIX (ASR extends the SAME opener) or high overall token overlap.
    ta, tb = _norm_fuzzy(a), _norm_fuzzy(b)
    if not ta or not tb:
        return False
    # common prefix length
    pre = 0
    for x, y in zip(ta, tb):
        if x == y:
            pre += 1
        else:
            break
    shorter = min(len(ta), len(tb))
    if pre >= 4 or (shorter >= 3 and pre >= shorter):        # one is a prefix-extension of the other
        return True
    inter = len(set(ta) & set(tb))
    union = len(set(ta) | set(tb)) or 1
    return (inter / union) >= 0.6                            # or high token overlap


def _fuzzy_overlap_count(inc_users, j_users):
    n = 0
    for iu in inc_users:
        if any(_fuzzy_same_utterance(iu, ju) for ju in j_users):
            n += 1
    return n


def is_subset_of_another(call_id, my_users, others):
    """ONE-summary backstop (kept for back-compat): return the call_id of a DIFFERENT journal
    whose user-turns are a superset of mine (mine ⊆ theirs), else None. `others`:
    list[(call_id, [user_texts])]. NOTE: superseded by pick_injection_winner for the live path —
    this exact-subset check is defeated by ASR re-transcription (gm 2026-08-10)."""
    mine = set(_norm_user(u) for u in my_users if _norm_user(u))
    if not mine:
        return None
    for oid, ousers in others:
        if oid == call_id:
            continue
        theirs = set(_norm_user(u) for u in ousers if _norm_user(u))
        if mine <= theirs and len(theirs) > len(mine):
            return oid
    return None


def find_matching_call(incoming_users, live_journals):
    """Resolve which existing LIVE call an incoming request belongs to, by FUZZY USER-TURN OVERLAP
    — robust to ElevenLabs reshaping/truncating history mid-call, proxy restarts, AND ASR
    RE-TRANSCRIPTION of the same utterance with different/longer text (gm 2026-08-10: exact-string
    matching split one physical call into two journals when the opener was re-transcribed longer
    ~3s in). `incoming_users`: ordered list[str]. `live_journals`: list[(call_id, [user_texts])].
    Returns the best-matching call_id, or None to start a fresh call."""
    inc = [_norm_user(u) for u in incoming_users if _norm_user(u)]
    if not inc:
        return None
    best, best_score = None, 0
    for call_id, jusers in live_journals:
        js = [_norm_user(u) for u in jusers if _norm_user(u)]
        if not js:
            continue
        overlap = _fuzzy_overlap_count(inc, js)             # FUZZY, not exact
        if overlap == 0:
            continue
        same_first = _fuzzy_same_utterance(inc[0], js[0])   # matching opener = strongest signal
        score = overlap + (1000 if same_first else 0)
        if score > best_score:
            best_score, best = score, call_id
    return best


def pick_injection_winner(target_id, journals, proximity_s=90):
    """Injection backstop (gm 2026-08-10): among all journals in the SAME physical call as
    `target_id` — clustered by fuzzy-matching opener AND started_at within proximity_s — return
    the ONE that should inject to gm: the LONGEST (most user turns), latest-started on a tie. If
    `target_id` is that winner → inject; else it's a shard → suppress. This holds even when
    resolution still splits (defense in depth) and never injects the thin first-to-finalize shard.
    `journals`: list[(call_id, started_at, [user_texts])]."""
    tgt = next((j for j in journals if j[0] == target_id), None)
    if tgt is None:
        return True                                         # unknown → don't suppress
    _, t_start, t_users = tgt
    t_open = next((u for u in t_users if _norm_user(u)), "")
    cluster = [tgt]
    for cid, cstart, cusers in journals:
        if cid == target_id:
            continue
        c_open = next((u for u in cusers if _norm_user(u)), "")
        if abs(cstart - t_start) <= proximity_s and t_open and c_open \
                and _fuzzy_same_utterance(t_open, c_open):
            cluster.append((cid, cstart, cusers))
    if len(cluster) == 1:
        return True                                         # not fragmented → inject
    # winner = most user turns, latest started on a tie
    def _key(j):
        return (sum(1 for u in j[2] if _norm_user(u)), j[1])
    winner = max(cluster, key=_key)
    return winner[0] == target_id


class CallJournal:
    def __init__(self, dir, page="", call_id=None):
        self.dir = Path(dir)
        self.call_id = call_id or ("vc_" + secrets.token_hex(8))   # 16 hex chars, all regex-legal
        self.page = page
        self._data = {
            "call_id": self.call_id, "started_at": time.time(),
            "page": page, "status": "live", "turns": [], "summary": None,
        }
        self._flush()

    @property
    def path(self) -> Path:
        return self.dir / f"{self.call_id}.json"

    def _flush(self):
        _atomic_write(self.path, self._data)

    def add_turn(self, role, text):
        turns = self._data["turns"]
        # dedup: a retry / re-sent identical turn (same role + text back-to-back) is not a new turn.
        if turns and turns[-1].get("role") == role and turns[-1].get("text") == (text or "")[:2000]:
            return
        turns.append({"role": role, "text": (text or "")[:2000], "ts": time.time()})
        self._flush()

    def add_tool(self, tool, input, result, status="done"):
        self._data["turns"].append({
            "role": "tool", "tool": tool, "input": input,
            "result": (result or "")[:2000], "status": status, "ts": time.time()})
        self._flush()

    def finalize(self, summary):
        self._data["summary"] = summary
        self._data["status"] = "ended"
        self._data["ended_at"] = time.time()
        self._flush()
