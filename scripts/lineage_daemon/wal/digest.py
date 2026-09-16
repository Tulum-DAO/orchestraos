"""Deterministic `wal digest` renderer (spec §3.3, DP-1/DP-4). NO LLM anywhere.

Green (a FRESH successor with full context headroom) reads this brief and drills
into `body_ref`s as needed — inverting the fatal allocation where the saturated
predecessor did the comprehension.

SAFETY (the centerpiece — the WAL is a contagion vector; the court glitch spreads
by IMITATION of ingested model-voice text):
  * CLASS defense (signature-independent): model-voice bodies are NEVER resolved
    into the digest. `response`/`thinking` render as STRUCTURAL rows (seq + kind +
    the structural summary, no content). `tool_call` renders as a STRUCTURED row
    (name + result-status) — its raw args are never resolved. So model-voice can
    never appear verbatim in any layer, by construction.
  * WORLD-OUTPUT drill: only `tool_result`/`file_mod` (world output, not model
    voice) may surface body content, and only AFTER passing the scrub boundary.
  * SCRUB boundary (known-instance defense): every text fragment placed in the
    digest passes `scrub(text) -> (clean, contaminated)`; a final full-text scrub
    pass guarantees ZERO occurrences of a detected signature. A tripped scrub sets
    `contaminated=true` and hard-excludes the offending span (records its seq).

Layers (§3.3): (a) mission refs (static prompts/<root>.md), (b) decision timeline
(prompt/response structural rows), (c) working-set (file_mod/git paths+shas),
(d) in-flight tail (last N tool_call/tool_result).

DP-4: the digest is capped at 30% of the runtime ceiling (deterministic token
estimate). On overflow it truncates in fixed priority — retain mission > working
> timeline > in-flight-tail; drop the lowest priority (tail) oldest-first — and
records `truncated=true` + the dropped seqs so Green can still drill via body_ref.
"""

DIGEST_CAP_FRACTION = 0.30
DEFAULT_TAIL_N = 20
_TOOL_RESULT_PREVIEW = 200


def _noop_scrub(text):
    return text, False


def _est_tokens(text):
    # deterministic char/4 heuristic (no LLM/model call in the loop)
    return len(text) // 4


# drop-rank: higher = dropped first. mission never dropped.
_RANK_MISSION = 0
_RANK_WORKING = 1
_RANK_TIMELINE = 2
_RANK_TAIL = 3


def render_digest(store, lineage_root, *, since_seq=0, ceiling_tokens=800_000,
                  tail_n=DEFAULT_TAIL_N, resolve_body=None, scrub=None,
                  mission_ref=None):
    scrub = scrub or _noop_scrub
    events = [r for r in store.events(lineage_root) if r["seq"] > since_seq]

    contaminated = False
    dropped_spans = []
    # each line: (rank, seq, text)
    lines = []

    def _scrub_add(rank, seq, text):
        nonlocal contaminated
        clean, tripped = scrub(text)
        if tripped:
            contaminated = True
            dropped_spans.append(seq)
            lines.append((rank, seq, f"[{seq}] <contaminated span excluded>"))
        else:
            lines.append((rank, seq, clean))

    # (a) mission refs — static file pointers, never model-voice content
    mission = mission_ref or f"prompts/{lineage_root}.md"
    lines.append((_RANK_MISSION, 0, f"MISSION {mission}"))

    # (b) decision timeline — prompt/response STRUCTURAL only (no body resolve)
    for r in events:
        if r["kind"] in ("prompt", "response"):
            _scrub_add(_RANK_TIMELINE, r["seq"],
                       f"[{r['seq']}] {r['kind']} {r['summary'] or ''}".rstrip())

    # (c) working set — file_mod/git summaries carry paths/shas (world refs)
    for r in events:
        if r["kind"] in ("file_mod", "git"):
            _scrub_add(_RANK_WORKING, r["seq"],
                       f"[{r['seq']}] {r['kind']} {r['summary'] or ''}".rstrip())

    # (d) in-flight tail — last N tool_call/tool_result
    tail_events = [r for r in events
                   if r["kind"] in ("tool_call", "tool_result")][-tail_n:]
    for r in tail_events:
        if r["kind"] == "tool_call":
            # STRUCTURED row: name (from summary) only; raw args never resolved
            _scrub_add(_RANK_TAIL, r["seq"],
                       f"[{r['seq']}] TOOL {r['summary'] or '?'}")
        else:  # tool_result = WORLD OUTPUT: scrub the FULL body, THEN truncate
            body = ""
            if resolve_body and r["body_ref"]:
                body = resolve_body(r["body_ref"]) or ""
            clean, tripped = scrub(body)   # scrub full body, not the preview
            if tripped:
                contaminated = True
                dropped_spans.append(r["seq"])
                lines.append((_RANK_TAIL, r["seq"],
                              f"[{r['seq']}] <contaminated span excluded>"))
            else:
                lines.append((_RANK_TAIL, r["seq"],
                              f"[{r['seq']}] RESULT "
                              f"{clean[:_TOOL_RESULT_PREVIEW]}".rstrip()))

    truncated = False
    cap = int(ceiling_tokens * DIGEST_CAP_FRACTION)

    def _full():
        return _render_text(lines, lineage_root, since_seq, contaminated, True)

    # DP-4: drop lowest priority (highest rank) oldest-first until the FULL
    # rendered digest is within 30% of the ceiling; mission is never dropped.
    while _est_tokens(_full()) > cap:
        candidates = [(rank, seq, i) for i, (rank, seq, _) in enumerate(lines)
                      if rank != _RANK_MISSION]
        if not candidates:
            break
        candidates.sort(key=lambda c: (-c[0], c[1]))  # highest rank, oldest seq
        _, seq, idx = candidates[0]
        lines.pop(idx)
        dropped_spans.append(seq)
        truncated = True

    text = _render_text(lines, lineage_root, since_seq, contaminated, truncated)
    # belt-and-suspenders: a final full-text scrub guarantees ZERO signature
    # occurrences in the emitted digest regardless of which layer produced them.
    text, final_tripped = scrub(text)
    if final_tripped:
        contaminated = True

    return {
        "lineage_root": lineage_root,
        "since_seq": since_seq,
        "max_seq": store.max_seq(),
        "contaminated": contaminated,
        "truncated": truncated,
        "dropped_spans": sorted(set(dropped_spans)),
        "text": text,
    }


def _render_text(lines, lineage_root, since_seq, contaminated, truncated):
    out = [f"# WAL DIGEST {lineage_root} (since_seq={since_seq})",
           f"contaminated={contaminated} truncated={truncated}"]
    for rank, section in ((_RANK_MISSION, "MISSION"),
                          (_RANK_TIMELINE, "TIMELINE"),
                          (_RANK_WORKING, "WORKING-SET"),
                          (_RANK_TAIL, "IN-FLIGHT-TAIL")):
        rows = [t for rk, _, t in lines if rk == rank]
        if rows:
            out.append(f"## {section}")
            out.extend(rows)
    return "\n".join(out)
