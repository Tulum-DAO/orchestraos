#!/usr/bin/env python3
"""rotation_gate_manual.py — shared comprehension-gate helper for MANUAL rotations.

Phase B2 (spec 2026-08-16-initiative-pm-driver-design.md §6.5): the enforcement
ladder's machinery layer for the read-back+canary protocol (RED-TEAM Finding 0).
CONSUMES scripts/focus_registry/comprehension.py (platform-builder's lane — the
grading logic lives there, never here).

Contract:
  * author_canary(successor_id, questions)   — predecessor writes DAEMON-HELD
    canary Q+A to state/agent-handoffs/<id>.canary.json. Expected answers live
    ONLY in this artifact; the successor-visible init carries QUESTIONS only.
  * questions_only(successor_id)             — the q strings for init injection.
  * record_readback(successor_id, readback, answers, ground_truth) — grades the
    successor's own-words read-back + canary answers via the real
    comprehension.check_comprehension, writes <id>.comprehension.json, returns it.
  * comprehension_passed(successor_id)       — FAIL-CLOSED artifact reader
    (missing/corrupt/false => False). Same artifact WS3's rotation_gate reads —
    one gate, two paths (manual + auto).
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from focus_registry.comprehension import check_comprehension  # noqa: E402

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
HANDOFFS_DIR = ORCHESTRA_DIR / "state" / "agent-handoffs"


def _handoffs_dir() -> Path:
    """Indirection so tests monkeypatch HANDOFFS_DIR on the module."""
    return Path(HANDOFFS_DIR)


def _canary_path(successor_id: str) -> Path:
    return _handoffs_dir() / f"{successor_id}.canary.json"


def _comprehension_path(successor_id: str) -> Path:
    return _handoffs_dir() / f"{successor_id}.comprehension.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cv4_observe_rotation_artifact(p, obj):
    """CV4 W6 write-fence SHADOW routing (observe-only; NEVER alters the write).
    rotation_gate_manual writes TWO handoff artifacts via this one helper —
    <id>.canary.json (author_canary) + <id>.comprehension.json (record_readback)
    — so routing here covers BOTH targets. store='handoff-artifact', key=filename
    so the ledger distinguishes canary vs comprehension. Reuses the W4/W5 checked-
    RETURN pattern: observe_write RETURNS 1=logged / 0=failed / -1=disabled — we
    check the RETURN, not absence-of-exception (the W7 dead-except trap). rc==0 =>
    ONE visible stderr line; the real rotation write ALWAYS proceeds. Fully wrapped:
    a broken/missing guard can never break a manual rotation. Off-switches
    (kill-file + CV4_WRITE_FENCE_MODE env) live in write_fence."""
    try:
        import importlib.util
        _wf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "continuity", "write_fence.py")
        _spec = importlib.util.spec_from_file_location("cv4_write_fence", _wf_path)
        _wf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_wf)
        rc = _wf.observe_write(store="handoff-artifact", key=Path(p).name,
                               record=obj if isinstance(obj, dict) else {},
                               on_disk={}, writer="rotation-gate-manual")
        if rc == 0:
            sys.stderr.write(
                "CV4 W6 observe error (rate-limited): shadow write failed for "
                "handoff-artifact/%s (real rotation write proceeded)\n" % Path(p).name)
    except Exception as e:
        sys.stderr.write("CV4 W6 observe error (rate-limited): %r "
                         "(real rotation write proceeded)\n" % e)


def _atomic_write(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    _cv4_observe_rotation_artifact(p, obj)  # CV4 W6: shadow observe both targets
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, str(p))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


_ANSWER_KEYS = {"answer", "expected", "expected_answer", "answers", "key"}

# The three resolvable pointer forms (author-side mirror of resolve_pointer,
# DEC-1787728346: prose pointers are ungradeable and must fail at author time —
# the only moment they are cheap to fix). Prefix optional, like the resolver.
_POINTER_FORM = re.compile(
    r"^(?:jsonl:)?(?:"
    r"msg_[0-9a-z_]+"                                   # jsonl:msg_<id>
    r"|turn-\d+"                                        # jsonl:turn-<n>
    r"|\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?"                  # jsonl:<ISO>..<ISO>
    r"\.\.\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?"
    r")$", re.IGNORECASE)


class AmbiguousGraderInput(RuntimeError):
    """The graded answers could not be matched to canary ids at all. A grade of
    'all missed' from unmatchable input is a HARNESS signal, not a result — it is
    indistinguishable from a genuinely incompetent successor (P2-7, the artifact
    that made gm re-grade the g9 gate by hand)."""


# A POINTER CAN LEAK AS BADLY AS AN ANSWER (found by orchestraos-app-dev-v14 while
# complying with the new schema, msg_9abbaee2). Its case: the question asks "what
# was the msg_store row id that carried it?" and the natural pointer is
# `jsonl:msg_demo0003_0000003` — the answer, inside the locator. Deleting the
# answer key accomplishes nothing if the pointer quietly becomes one, and it would
# have reintroduced the leak for exactly the questions most worth asking.
#
# Mechanical rule: when a question ASKS FOR an identifier, its pointer may not BE
# one. The non-disclosing forms (turn index, ISO range) locate the fact without
# naming it. Not a blanket ban — a msg-id pointer stays the clearest locator for a
# question that is not asking for an identifier.
_ID_LITERAL = re.compile(
    r"(msg_[0-9a-z]{6,}|DEC-\d{6,}|[0-9a-f]{7,40}\b|apr_[0-9a-z]{6,})", re.IGNORECASE)
_ASKS_FOR_ID = re.compile(
    r"\b(id|ids|hash|sha|commit|row id|message id|msg id|agent id|dec\b|"
    r"which (?:message|msg|row|commit|dec|agent)|name the)\b", re.IGNORECASE)


def pointer_discloses(question: str, pointer: str) -> bool:
    """True when the pointer would hand over what the question asks for."""
    return bool(_ASKS_FOR_ID.search(str(question or ""))
                and _ID_LITERAL.search(str(pointer or "")))


def author_canary(successor_id: str, questions: list) -> str:
    """Predecessor authors canary QUESTIONS + SOURCE POINTERS. There is NO answer
    key — anywhere, ever (the operator ruling 2026-08-18).

    `questions` = [{"q": str, "source_pointer": str}, ...] where source_pointer
    names WHERE the fact lives in the predecessor's jsonl (`jsonl:msg_<id>`,
    `jsonl:turn-<n>`, or `jsonl:<ISO>..<ISO>`). The grader verifies the
    successor's own words against that transcript AT GRADE TIME.

    Why not a vault: storing answers ANYWHERE protects a secret instead of
    deleting it, and a lock can be picked, mis-pathed, or copied by the next
    refactor. With no answer key, leaking this artifact is harmless by
    construction — it only tells the successor to go read the transcript, which is
    exactly what protocol-v2 wants it to do."""
    if not successor_id or not isinstance(questions, list):
        raise ValueError("author_canary needs a successor_id and a questions list")
    qs = []
    for q in questions:
        if not isinstance(q, dict) or not q.get("q"):
            raise ValueError(f"canary question needs 'q': {q!r}")
        leaked = [k for k in q if str(k).strip().lower() in _ANSWER_KEYS]
        if leaked:
            raise ValueError(
                f"canary carries an ANSWER KEY ({', '.join(sorted(leaked))}) — "
                f"there is no answer key, ever. Give a source_pointer into your "
                f"jsonl instead; the grader verifies against the transcript.")
        if not str(q.get("source_pointer") or "").strip():
            raise ValueError(
                f"canary question needs a source_pointer into your jsonl: {q!r} — "
                f"an unpointable fact is not gradeable and was never a fair "
                f"question")
        if not _POINTER_FORM.match(str(q["source_pointer"]).strip()):
            raise ValueError(
                f"canary source_pointer is not mechanically resolvable: "
                f"{q['source_pointer']!r}. A prose pointer can never be graded "
                f"(gm-gen28 class, DEC-1787728346). Use one of the resolver's "
                f"forms — 'jsonl:msg_<id>', 'jsonl:turn-<n>', or "
                f"'jsonl:<ISO>..<ISO>' (the 'jsonl:' prefix is optional, "
                f"matching resolve_pointer's leniency). turn-<n> counts TEXT-BEARING "
                f"rows, 1-based. If the fact lives OUTSIDE your transcript (files, "
                f"dream/facts, recordings), cite the msg_store row or commit sha that "
                f"carries it — the grader grounds in transcript ids, never in files "
                f"(gm ruling msg_f937b086, 2026-09-16).")
        if pointer_discloses(q["q"], q["source_pointer"]):
            raise ValueError(
                f"pointer DISCLOSES the answer: the question asks for an "
                f"identifier and the pointer {q['source_pointer']!r} is one. Use a "
                f"non-disclosing locator instead — 'jsonl:turn-<n>' or "
                f"'jsonl:<ISO>..<ISO>' — which locates the fact without naming it.")
        qs.append({"q": str(q["q"]), "source_pointer": str(q["source_pointer"])})
    obj = {"successor": successor_id, "created_at": _now_iso(), "questions": qs}
    path = _canary_path(successor_id)
    _atomic_write(path, obj)
    return str(path)


def load_canary(successor_id: str) -> dict:
    """The daemon-held canary artifact, or {} (missing/corrupt => empty)."""
    try:
        obj = json.loads(_canary_path(successor_id).read_text())
        return obj if isinstance(obj, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def questions_only(successor_id: str) -> list:
    """JUST the question strings (safe to inject into the successor's init —
    never the expected answers). [] when no/corrupt canary file (fail-open for
    injection: a missing canary must never break a spawn)."""
    out = []
    for q in load_canary(successor_id).get("questions", []) or []:
        if isinstance(q, dict) and q.get("q"):
            out.append(str(q["q"]))
    return out


def _canary_ground_truth(successor_id: str, ground_truth: dict) -> list:
    """comprehension.grade_canary shape: [{"id", "answer"}] keyed q1..qn.

    THERE IS NO STORED ANSWER KEY (the operator ruling 2026-08-18). Ground truth is now
    derived AT GRADE TIME from the predecessor's transcript region that each
    question points at — the transcript is immutable and cannot drift from
    itself, unlike a static answer string an author might paraphrase wrong. An
    explicit ground_truth["canary"] (a supervisor grading in-context) still
    wins."""
    explicit = (ground_truth or {}).get("canary")
    if explicit:
        return explicit
    qs = load_canary(successor_id).get("questions", []) or []
    tpath = (ground_truth or {}).get("transcript_path")
    out = []
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            continue
        region = resolve_pointer(tpath, q.get("source_pointer")) if tpath else ""
        out.append({"id": f"q{i}", "answer": region, "from_pointer": True,
                    # the question text rides along so the citation-grounding
                    # grader can exclude question tokens (anti-echo,
                    # DEC-1787728346) — never an answer key.
                    "question": str(q.get("q") or "")})
    return out


_STOP = {"the", "and", "that", "this", "with", "from", "into", "were", "was",
         "have", "has", "for", "not", "but", "you", "your", "its", "it's", "are",
         "a", "an", "of", "to", "in", "on", "at", "by", "is", "as", "so", "it",
         "my", "our", "their", "what", "when", "why", "how", "did", "does"}


def _terms(text: str) -> set:
    import re as _re
    return {w for w in _re.findall(r"[a-z0-9_.]{4,}", str(text).lower())
            if w not in _STOP}


def _epoch(ts, base_date=None):
    """ISO-8601 -> UTC epoch seconds, or None. Tolerates a trailing 'Z', any
    offset, any subsecond precision, and time-only specs if base_date given."""
    if not ts:
        return None
    t = str(ts).strip()
    # Strip common leading noise like "ISO range ", "ISO ", "range "
    t = re.sub(r"^(?:iso\s*(?:range\s*)?|range\s*)", "", t, flags=re.IGNORECASE).strip()
    t = t.replace("Z", "+00:00")
    if "T" not in t and base_date and ":" in t:
        t = f"{base_date}T{t}"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def resolve_pointer(transcript_path, pointer: str) -> str:
    """Resolve a canary source_pointer to the TEXT of that region of the
    predecessor's jsonl. Supported forms:
        jsonl:msg_<id>              — the entry whose uuid/id contains <id>
        jsonl:turn-<n>              — the nth entry (1-based)
        jsonl:<ISO>..<ISO>          — every entry in the timestamp range
    Returns "" when the pointer does not resolve; the CALLER treats that as a
    defect in the canary, never as a failure of the successor."""
    try:
        lines = Path(transcript_path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    spec = str(pointer or "").strip()
    spec = spec.split(":", 1)[1] if spec.lower().startswith("jsonl:") else spec
    spec = spec.strip()
    rows = []
    for ln in lines:
        try:
            rows.append(json.loads(ln))
        except (json.JSONDecodeError, ValueError):
            continue

    def _text(d):
        payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        msg = d.get("message") if isinstance(d.get("message"), dict) else payload
        c = msg.get("content") or d.get("content")
        if isinstance(c, str):
            return c
        out = []
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    t = b.get("text") or b.get("thinking") or b.get("input") or ""
                    if t:
                        out.append(str(t))
        elif isinstance(c, dict):
            t = c.get("text") or c.get("thinking") or ""
            if t:
                out.append(str(t))
        if not out and isinstance(payload.get("text"), str):
            out.append(payload["text"])
        return "\n".join(out)

    if ".." in spec:
        parts = [x.strip() for x in spec.split("..", 1)]
        base_d = None
        m = re.search(r"\d{4}-\d{2}-\d{2}", parts[0])
        if m:
            base_d = m.group(0)
        lo = _epoch(parts[0])
        hi = _epoch(parts[1], base_date=base_d)
        if lo is None or hi is None:
            return ""
        hits = []
        for r in rows:
            t = _epoch(r.get("timestamp") or (r.get("payload") or {}).get("create_time"))
            if t is not None and lo <= t <= hi:
                hits.append(r)
    elif spec.lower().startswith("turn-"):
        try:
            i = int(spec.split("-", 1)[1])
        except ValueError:
            return ""
        # turn-<n> = the nth TEXT-BEARING row. Codex rollouts interleave session_meta /
        # event_msg / token_usage / turn_context rows with the messages (gpt-6-astra-agent
        # g3->g4 2026-09-16: turn-1 hit session_meta -> "" -> unpointable on a real readback);
        # claude/gemini transcripts are unaffected (every row there carries text anyway).
        turns = [r for r in rows if _text(r).strip()]
        hits = [turns[i - 1]] if 1 <= i <= len(turns) else []
    else:
        hits = [r for r in rows
                if spec in str(r.get("uuid") or "")
                or spec in str(r.get("id") or "")
                or spec in str((r.get("payload") or {}).get("id") or "")]
    return "\n".join(_text(r) for r in hits).strip()


def _normalize_answers(canary_qs: list, answers) -> dict:
    """P2-7: accept list[str], dict{id: answer}, and list[{id, answer}] alike.
    The old code did zip(canary, list(answers)), which turns a DICT into its KEYS
    — every graded answer became '1'..'5': well-shaped, content-free, and scored
    as a total miss no different from an incompetent successor."""
    ids = [f"q{i}" for i, _ in enumerate(canary_qs, 1)]
    if isinstance(answers, dict):
        out = {}
        for k, v in answers.items():
            key = str(k) if str(k) in ids else f"q{k}"
            if key in ids:
                out[key] = str(v)
        if not out:
            raise AmbiguousGraderInput(
                f"none of the answer keys {sorted(map(str, answers))} match canary "
                f"ids {ids} — refusing to report a total miss from unmatchable "
                f"input")
        return out
    seq = list(answers or [])
    if seq and all(isinstance(a, dict) for a in seq):
        out = {}
        for a in seq:
            key = str(a.get("id") or "")
            key = key if key in ids else f"q{key}"
            if key in ids:
                out[key] = str(a.get("answer") or a.get("text") or "")
        if not out:
            raise AmbiguousGraderInput(
                f"answer records carry no ids matching {ids}")
        return out
    if len(seq) not in (0, len(ids)):
        raise AmbiguousGraderInput(
            f"{len(seq)} answers for {len(ids)} canary questions — refusing to "
            f"zip-truncate into a partial grade")
    return {i: str(a) for i, a in zip(ids, seq)}


def canary_evidence(successor_id: str, answers, transcript_path=None) -> dict:
    """Per-question GRADING EVIDENCE derived from the predecessor's transcript at
    grade time. Deliberately advisory: it reports what it can justify (did the
    pointer resolve, which distinctive terms the successor's own words share with
    the pointed region) and never emits a silent semantic pass/fail. The ruling
    stays with the supervisor; this is the evidence the supervisor rules ON."""
    qs = load_canary(successor_id).get("questions", []) or []
    norm = _normalize_answers(qs, answers)
    # advisory_only: since the DEC-1787728346 recalibration these overlap
    # numbers have ZERO effect on the verdict (grounding is transcript-wide);
    # the label exists because a reviewer diagnosed a live grade from this
    # block's overlap_score (gen29 datum, 08-26) — read detail.canary instead.
    out = {"advisory_only": True}
    for i, q in enumerate(qs, 1):
        qid = f"q{i}"
        ptr = (q or {}).get("source_pointer")
        region = resolve_pointer(transcript_path, ptr) if transcript_path else ""
        ans = norm.get(qid, "")
        rec = {"question": (q or {}).get("q"), "source_pointer": ptr,
               "pointer_resolved": bool(region), "answered": bool(ans.strip())}
        if not region:
            # The canary is at fault here, not the candidate.
            rec["defect"] = "unpointable-canary"
            rec["overlap_score"] = None
            rec["overlap_terms"] = []
        else:
            rt, at = _terms(region), _terms(ans)
            shared = sorted(rt & at)
            rec["overlap_terms"] = shared
            rec["overlap_score"] = round(len(shared) / max(1, len(rt)), 3)
            rec["region_chars"] = len(region)
        out[qid] = rec
    return out


_VOUCH_FIELDS = ("init_ref", "operator_sid", "grade_linkage")


def _identity_vouch(identity) -> "dict | None":
    """Validate + normalize a Piece-1 identity vouch (DEC-1787687601). Returns the
    3-field vouch dict, or None when no identity was provided. A PARTIAL vouch is
    refused (ValueError) — a vouch missing a field cannot vouch identity, and a
    half-written block would be silently trusted by a mechanical Key-1 grader
    (the exact 'success that isn't' class). All three of init_ref (the
    /tmp/agent-init-<id>.md the by-reference spawn Read), operator_sid (the
    successor's asserted sid), and grade_linkage (the canary/commit that ties the
    grade to this rotation) are required together."""
    if identity is None:
        return None
    if not isinstance(identity, dict):
        raise ValueError("identity vouch must be a dict of "
                         f"{_VOUCH_FIELDS} or None")
    missing = [k for k in _VOUCH_FIELDS if not str(identity.get(k) or "").strip()]
    if missing:
        raise ValueError(
            f"identity vouch is PARTIAL (missing {', '.join(missing)}) — a vouch "
            f"missing any of {_VOUCH_FIELDS} cannot vouch identity for a mechanical "
            f"Key-1 grader; provide all three or None (never a half-populated block)")
    return {k: str(identity[k]) for k in _VOUCH_FIELDS}


def record_readback(successor_id: str, readback: str, answers: list,
                    ground_truth: dict, *, identity=None,
                    mode: str = "supervised", successor_sid: str = None) -> dict:
    """Grade the successor's own-words read-back (free text) + ordered canary
    answers against DAEMON-HELD ground truth ({goal?, guards?, open_loops,
    hazards} from the handoff + canary expected answers). Calls the REAL
    focus_registry.comprehension.check_comprehension. Writes
    <id>.comprehension.json atomically; returns the artifact dict.

    `identity` (Piece 1, DEC-1787687601): an optional {init_ref, operator_sid,
    grade_linkage} vouch written into the artifact so a MECHANICAL Key-1 grader can
    vouch the successor's identity without a human (closes the declared_identity=
    None hole for by-reference `Read /tmp/agent-init-*.md` spawns). All-or-nothing."""
    vouch = _identity_vouch(identity)     # validate BEFORE any grade work
    ground_truth = dict(ground_truth or {})
    tpath = ground_truth.get("transcript_path")
    if tpath and not ground_truth.get("transcript_text") and os.path.exists(tpath):
        try:
            ground_truth["transcript_text"] = Path(tpath).read_text(errors="replace")
        except OSError:
            pass
    canary_gt = _canary_ground_truth(successor_id, ground_truth)
    ground_truth["canary"] = canary_gt
    # A canary authored from a synthesized baton (`orchestra rotate --synthesize`) carries
    # synthesized=true: the grader lowers ONLY the distinct-evidence floor to 1 (mode stays
    # strict — the gate invariant is untouched). Recorded in the artifact below.
    if load_canary(successor_id).get("synthesized"):
        ground_truth["synthesized"] = True
    # citation-grounding mode (DEC-1787728346): "supervised" tolerates <=1 WEAK
    # answer; "strict" (zero WEAK + region corroboration) is the ARMING
    # PRECONDITION — unattended auto-retire may only consume strict grades.
    ground_truth.setdefault("mode", mode)

    # The manual-path read-back is one own-words free-text blob; coverage of
    # each ground-truth class is checked against the whole text (the deep
    # open_loops/hazards tokens must appear SOMEWHERE in the successor's words).
    text = str(readback or "")
    evidence = {
        "readback": {"goal": text, "guards": [text], "open_loops": [text], "hazards": [text]},
        # P2-7: normalize the answer shape instead of zip()ing a dict into its keys.
        "canary_answers": _normalize_answers(
            load_canary(successor_id).get("questions", []) or canary_gt, answers),
    }
    result = check_comprehension(evidence, ground_truth)
    # Evidence the supervisor rules ON (never a silent semantic verdict): did each
    # pointer resolve, and which distinctive terms the successor's own words share
    # with the pointed transcript region.
    try:
        evid = canary_evidence(successor_id, answers,
                               transcript_path=(ground_truth or {}).get("transcript_path"))
    except AmbiguousGraderInput as e:
        evid = {"error": str(e)}
    _passed = bool(result.get("comprehended"))
    artifact = {
        # supervised|strict (DEC-1787728346): consumers arming auto-retire MUST
        # reject non-strict artifacts; recorded so the constraint is checkable.
        "mode": str(ground_truth.get("mode") or "supervised"),
        "synthesized": bool(ground_truth.get("synthesized")),
        # SINGLE DERIVATION (Piece 1, DEC-1787687601): `pass` (bool) is what the
        # completion path reads (completion_fn / comprehension_passed); `result`
        # ("PASS"|"FAIL") mirrors it for the mechanical Key-1 / lineage_gate grader
        # that reasons in verdicts. Both come from the ONE `_passed` value so they
        # can never disagree (a two-keys-for-one-truth drift vector otherwise).
        "pass": _passed,
        "result": "PASS" if _passed else "FAIL",
        "canary_evidence": evid,
        "detail": result,
        "graded_at": _now_iso(),
        "successor": successor_id,
        "graded_by": "rotation_gate_manual",
    }
    if vouch is not None:
        artifact["identity_vouch"] = vouch
    # D9 provenance (DEC-1787789209): stamp the successor's live session_id as the
    # IMMUTABLE authoring sid so the completion path can assert live-sid ==
    # provenance-sid — defeating a killed+name-reused pane re-authoring the artifact.
    # Absent when not provided (a legacy artifact; the completion path treats a
    # missing provenance sid as fail-closed NOT-ready).
    if successor_sid:
        artifact["provenance_sid"] = str(successor_sid)
    _atomic_write(_comprehension_path(successor_id), artifact)
    return artifact


def identity_vouch(successor_id: str) -> dict:
    """Read the Piece-1 identity vouch from <id>.comprehension.json, or {} when
    absent/unreadable (fail-closed: no vouch => a mechanical Key-1 grader must fall
    back to the human operator-assertion, never silently trust an unvouched sid)."""
    if not successor_id:
        return {}
    try:
        obj = json.loads(_comprehension_path(successor_id).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    v = obj.get("identity_vouch") if isinstance(obj, dict) else None
    return v if isinstance(v, dict) else {}


def comprehension_passed(successor_id: str) -> bool:
    """FAIL-CLOSED reader of <id>.comprehension.json: missing, unreadable,
    corrupt, or anything but a literal pass=true => False."""
    if not successor_id:
        return False
    try:
        obj = json.loads(_comprehension_path(successor_id).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(obj, dict) and obj.get("pass") is True


# --- grade CLI (DEC-1787728346: the wiring that makes comprehension.json ------
# actually get produced — root cause 1 was that record_readback had no runnable
# entry point, so the artifact never existed and `passed` fail-closed FAILed
# every rotation).

_SECTION_HEAD = re.compile(r"^(?:#{1,6}\s*)?\*{0,2}(?:cq|q)(\d+)\b", re.IGNORECASE | re.MULTILINE)
_MD_HEADING = re.compile(r"^#{1,6}\s+(?!\*{0,2}(?:cq|q)\d+\b)", re.IGNORECASE | re.MULTILINE)


class GradeRefusal(RuntimeError):
    """A harness/input defect that makes grading impossible. DISTINCT from a
    graded FAIL: refusal writes NO artifact (fail-hold for a human), because a
    parse defect graded as author-fail would feed a false verdict to a gate
    that can auto-kill (and a fake-pass is worse). CLI exit code 2."""


def split_readback_sections(md: str, n_questions: int) -> dict:
    """Per-question answers from a readback md by the section convention
    `## Q<N>`, `**cq<N>`, or `**Q<N>` at line start (line-anchored so an in-body
    "**Q1**" reference cannot split an answer; multi-digit; each section bounded
    at the next markdown heading so trailing prose never inflates the last answer).
    Duplicate headers or a section set != {1..n_questions} => GradeRefusal."""
    hits = list(_SECTION_HEAD.finditer(md))
    out, seen = {}, []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(md)
        seg = md[m.start():end]
        stop = _MD_HEADING.search(seg, 1)
        if stop:
            seg = seg[:stop.start()]
        qid = f"q{int(m.group(1))}"
        seen.append(qid)
        out[qid] = seg
    expected = [f"q{i}" for i in range(1, n_questions + 1)]
    if len(seen) != len(set(seen)):
        dupes = sorted({q for q in seen if seen.count(q) > 1})
        raise GradeRefusal(
            f"duplicate answer sections {dupes} in the readback — refusing to "
            f"grade ambiguous input (no artifact written)")
    if set(out) != set(expected):
        raise GradeRefusal(
            f"readback sections {sorted(out)} do not match the canary's "
            f"questions {expected} — refusing to grade a partial/misshapen "
            f"readback (no artifact written). Pass --answers-file for "
            f"non-conventional layouts.")
    return out


def find_transcript_by_sid(sid: str) -> str:
    """The predecessor's transcript (Claude, Gemini, or Codex), located by sid. Exactly one
    match or GradeRefusal."""
    if not re.fullmatch(r"[0-9a-zA-Z_.-]{7,}", str(sid or "")):
        raise GradeRefusal(
            f"--predecessor-sid {sid!r} is not a valid sid — refusing to locate transcript")
    try:
        from sid_invariants import find_transcript
    except ImportError:
        from scripts.sid_invariants import find_transcript
    hit = find_transcript(sid)
    if not hit or not os.path.exists(hit):
        raise GradeRefusal(
            f"predecessor transcript for sid {sid!r} not found. "
            f"Pass --transcript <path> explicitly.")
    return hit


def _cli_grade(a) -> int:
    canary_qs = load_canary(a.successor_id).get("questions", []) or []
    if not canary_qs:
        raise GradeRefusal(
            f"no canary for {a.successor_id} "
            f"({_canary_path(a.successor_id)}) — author one first")
    tpath = a.transcript or find_transcript_by_sid(a.predecessor_sid or "")
    if not os.path.exists(tpath):
        raise GradeRefusal(f"transcript not found: {tpath}")
    rb_path = a.readback_file or str(
        _handoffs_dir() / f"{a.successor_id}.readback.md")
    try:
        readback = Path(rb_path).read_text()
    except OSError as e:
        raise GradeRefusal(f"readback unreadable: {e}")
    if a.answers_file:
        answers = json.loads(Path(a.answers_file).read_text())
    else:
        answers = split_readback_sections(readback, len(canary_qs))
    ground_truth = {}
    if a.ground_truth:
        ground_truth = json.loads(Path(a.ground_truth).read_text())
        if not isinstance(ground_truth, dict):
            raise GradeRefusal("--ground-truth must be a JSON object")
    ground_truth["transcript_path"] = tpath
    if a.strict:
        # --strict must WIN over any "mode" key in the ground-truth file — an
        # operator who typed --strict ran the arming-grade, full stop
        # (review I-1: setdefault alone let a gt file silently downgrade it).
        ground_truth["mode"] = "strict"
    art = record_readback(a.successor_id, readback, answers, ground_truth,
                          mode="strict" if a.strict else "supervised",
                          successor_sid=getattr(a, "successor_sid", None))
    print(json.dumps({"pass": art["pass"], "result": art["result"],
                      "mode": art["mode"],
                      "artifact": str(_comprehension_path(a.successor_id))}))
    return 0 if art["pass"] else 1


if __name__ == "__main__":
    # CLI so shell machinery (spawn-agent.sh, gm's rotation checklist) can
    # consume without inline python. Exit codes: 0 graded PASS, 1 graded FAIL,
    # 2 REFUSED (no artifact — harness/input defect, hold for a human).
    import argparse
    ap = argparse.ArgumentParser(description="manual rotation comprehension gate")
    ap.add_argument("cmd", choices=["questions", "passed", "grade"])
    ap.add_argument("successor_id")
    ap.add_argument("--transcript", help="predecessor jsonl (explicit override)")
    ap.add_argument("--predecessor-sid", help="locate transcript by sid glob")
    ap.add_argument("--successor-sid",
                    help="the successor's live session_id, stamped as the D9 "
                         "immutable provenance sid in the artifact "
                         "(DEC-1787789209 completion path)")
    ap.add_argument("--readback-file",
                    help="default: state/agent-handoffs/<id>.readback.md")
    ap.add_argument("--answers-file",
                    help="explicit per-question answers JSON (wins over parsing)")
    ap.add_argument("--ground-truth",
                    help="optional JSON with goal/guards/open_loops/hazards")
    ap.add_argument("--strict", action="store_true",
                    help="zero-WEAK + region-corroboration mode (the "
                         "auto-retire arming precondition)")
    a = ap.parse_args()
    if a.cmd == "questions":
        for i, q in enumerate(questions_only(a.successor_id), 1):
            print(f"({i}) {q}")
    elif a.cmd == "grade":
        if not (a.transcript or a.predecessor_sid):
            print("grade needs --transcript or --predecessor-sid", file=sys.stderr)
            sys.exit(2)
        try:
            sys.exit(_cli_grade(a))
        except (GradeRefusal, AmbiguousGraderInput, ValueError,
                json.JSONDecodeError) as e:
            print(f"REFUSED (no artifact): {e}", file=sys.stderr)
            sys.exit(2)
    else:
        ok = comprehension_passed(a.successor_id)
        print("PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
