"""ctx_adapters — the provider-agnostic ctx-read registry (DEC v2, the operator #1).

`read_ctx(runtime, seat) -> (pct, fresh)` where ``pct`` is the NORMALIZED fraction
of USABLE context consumed (0..1) — the SAME meaning for every provider — and
``fresh`` says the read is live (not stale/absent). Each adapter owns its
normalization and is tested in ``ctx_adapters_test.py``.

The rotation CORE (build_obs) sources ctx ONLY through this registry, a plain dict
lookup keyed on the runtime string — so the core carries ZERO runtime-name
conditionals (the operator's structural rule; grep-enforced over the core files). Adding a
provider = one registry entry + one thin adapter that passes conformance; ZERO core
changes.

Adapters are THIN wrappers over the EXISTING readers (no new readers built):
  * detector-file  -> the live claude ctx detector /tmp/claude-ctx-<sid>.json
  * codex          -> codex_context.get_codex_session_context (rollout tokens, reliable)
  * gemini         -> gemini_context.get_gemini_agents_status (approx tokens / 1M window)

Fail-soft: any error / seat-not-found / unknown runtime => (None, False), so the beat
falls back down its ladder and NEVER crashes on a reader hiccup (the beat firewall
still wraps this, but adapters must not raise on the common miss).
"""
import json
import os
import time

# The default provider name lives HERE (an adapter file), never in the core — build_obs
# imports it so a runtime-less agent still resolves without a literal in the core.
DEFAULT_RUNTIME = "claude"

DEFAULT_CTX_TTL_S = 120.0  # a detector file older than this is treated as UNKNOWN


# ---- normalization (each adapter's conversion to fraction-of-usable-context 0..1) ----

def normalize_from_pct(used_pct):
    """A reader that already reports a used PERCENT (0..100) -> fraction (0..1),
    clamped into [0,1] (a malformed >100/<0 must never trip a spurious ctx:swap)."""
    if not isinstance(used_pct, (int, float)) or used_pct != used_pct:  # non-num or NaN
        return None
    return max(0.0, min(100.0, float(used_pct))) / 100.0


def normalize_from_tokens(tokens, window):
    """A reader that reports raw tokens + a usable window -> fraction (0..1). This is the
    gm-required pinned semantic (e.g. codex 118899/258400 -> ~0.46), clamped to [0,1]."""
    if not isinstance(tokens, (int, float)) or not isinstance(window, (int, float)):
        return None
    if window <= 0 or tokens != tokens or window != window:  # guard /0 and NaN
        return None
    return max(0.0, min(1.0, float(tokens) / float(window)))


# ---- per-provider read_ctx adapters (thin wrappers over existing readers) ----

def read_ctx_detectorfile(seat, *, detector_dir=None, sid=None, now=None,
                          ttl_s=None, **_):
    """Claude adapter: read the live detector file /tmp/claude-ctx-<sid>.json (used_pct
    0..100) and NORMALIZE to (0..1, fresh). Absent/unreadable/malformed/STALE => (None,
    False) so build_obs falls back — never a fabricated 0. (Moved verbatim from the old
    bg_beat._read_detector_ctx so the claude path is behavior-identical.)"""
    if not sid:
        return (None, False)
    now = time.time() if now is None else now
    ttl_s = DEFAULT_CTX_TTL_S if ttl_s is None else ttl_s
    path = os.path.join(detector_dir or "/tmp", f"claude-ctx-{sid}.json")
    try:
        with open(path) as fh:
            d = json.load(fh)
        up = d.get("used_pct")
        ts = d.get("timestamp")
    except (OSError, ValueError, TypeError):
        return (None, False)
    frac = normalize_from_pct(up)
    if frac is None:
        return (None, False)
    if isinstance(ts, (int, float)) and (now - ts) > ttl_s:
        return (None, False)  # stale: a dead/blind occupant left an ancient file
    return (frac, True)


def read_ctx_codex(seat, **_):
    """Codex adapter: codex_context.get_codex_session_context(seat) -> (used_pct, tokens,
    window) | None. NORMALIZE tokens/window (the reliable rollout reader, NOT the screen).
    seat-not-found / import failure => (None, False) (fail-closed)."""
    try:
        import codex_context  # noqa: E402  (lazy — a reader hiccup must not wedge the beat)
        info = codex_context.get_codex_session_context(seat)
    except Exception:  # noqa: BLE001 — fail-soft to a clean miss
        return (None, False)
    if not info:
        return (None, False)
    try:
        _pct, tokens, window = info
    except (TypeError, ValueError):
        return (None, False)
    frac = normalize_from_tokens(tokens, window)
    return (frac, True) if frac is not None else (None, False)


GEMINI_USABLE_WINDOW = 1_000_000  # gemini_context's context_limit (pct_of_1m_window base)


def resolve_gemini_cid(seat, *, retired_sids_fn=None):
    """Resolve a gemini seat's conversation_id (cid) by EFFECT: the brain transcript whose
    first USER_INPUT declares ``You are <seat>``. This is how a gemini seat self-identifies
    (its spawn prompt) — robust for ANY seat name, unlike gemini_context's hardcoded
    name-matcher (which leaves unnamed seats like demo-gemini-pred as 'unknown'). Returns
    the cid or None. Read-only; used by read_ctx and by the arm's register_sid_fn (#1)."""
    import glob
    import json as _json
    import os as _os
    import re as _re
    brain = _os.path.expanduser("~/.gemini/antigravity-cli/brain")
    # WORD-BOUNDARY match: 'you are <seat>' NOT followed by a word char or hyphen, so
    # seat 'a' does NOT match 'You are agy-ops' (the substring bug that made read_ctx('a')
    # return agy-ops's ctx + broke hermetic tests). The declaration is 'You are <seat>.'
    # / '<seat> ' / '<seat>\n'.
    # gen-suffix tolerant: 'You are <seat>-g<N>' / '-gen<N>' IS the seat (promoted green).
    pat = _re.compile(rf"you are {_re.escape(seat)}(?:-g(?:en)?\d+)?(?![\w-])")
    paths = glob.glob(f"{brain}/*/.system_generated/logs/transcript.jsonl")
    # DB-retired generations of this root are NEVER candidates (gm msg_faf46471).
    retired = _retired_set(seat, retired_sids_fn, "resolve_gemini_cid")
    for b in sorted(paths, key=_os.path.getmtime, reverse=True):
        if retired and b.split(f"{brain}/", 1)[1].split("/", 1)[0] in retired:
            continue
        try:
            with open(b, errors="ignore") as fh:
                for line in fh:
                    try:
                        d = _json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") == "USER_INPUT" and d.get("content"):
                        if pat.search(str(d["content"]).lower()):
                            # cid = the path segment right after brain/ (robust vs a
                            # hardcoded split index, which only works for /home/<user>).
                            return b.split(f"{brain}/", 1)[1].split("/", 1)[0]
                        break  # only the FIRST user input declares identity
        except OSError:
            continue
    return None


def read_ctx_gemini(seat, **_):
    """Gemini adapter: NORMALIZE estimated_tokens / 1M window for `seat`. Identity is
    resolved robustly — first by the seat's registered session_id (cid) if agent-sessions
    maps it (finding #1), else by the seat's ``You are <seat>`` brain declaration
    (resolve_gemini_cid) — then the status row is matched by conversation_id. Falls back to
    gemini_context's name-match for already-named seats. Seat unresolvable / not surfaced
    => (None, False) (fail-closed — the conformance arm catches it)."""
    try:
        import gemini_context  # noqa: E402  (lazy)
        reports = gemini_context.get_gemini_agents_status() or []
    except Exception:  # noqa: BLE001
        return (None, False)
    cid = resolve_gemini_cid(seat)
    for r in reports:
        if (cid is not None and r.get("conversation_id") == cid) or r.get("agent") == seat:
            frac = normalize_from_tokens(r.get("estimated_tokens"), GEMINI_USABLE_WINDOW)
            return (frac, True) if frac is not None else (None, False)
    return (None, False)  # not surfaced -> fail-closed


# ---- the registry: runtime string -> read_ctx callable (the DATA lookup) ----

CTX_ADAPTER_REGISTRY = {
    "claude": read_ctx_detectorfile,
    "codex": read_ctx_codex,
    "gemini": read_ctx_gemini,
}


def read_ctx(runtime, seat, **kw):
    """Registry entry point the core calls. Dict lookup on the runtime string; an unknown
    runtime OR any adapter error => (None, False) (fail-closed). No runtime-name
    conditionals — a data lookup, so the core stays provider-agnostic."""
    fn = CTX_ADAPTER_REGISTRY.get((runtime or "").strip().lower())
    if fn is None:
        return (None, False)
    try:
        return fn(seat, **kw)
    except Exception:  # noqa: BLE001 — fail-soft: never raise into the beat
        return (None, False)


# ---- green-progress registry: runtime -> green_progress(green_sid) -> int|None ----
# GOAL-FINDING (b): the verify stall-bound must measure the GREEN's OWN progress, keyed on
# the green's sid — NEVER the blue root's WAL (the false-stall the live fire surfaced: the
# green was ingesting on its own cid while the root WAL sat flat). A provider adapter reports
# the green conversation's monotonic step high-water; a data lookup on the runtime string
# keeps the core zero-runtime-literal, mirroring CTX_ADAPTER_REGISTRY.

def green_progress_gemini(green_sid):
    """Gemini green-progress: the green conversation db's step high-water (monotonic as the
    green ingests/works). Absent/unreadable/empty => None (the caller treats None as
    no-progress => the stall bound accrues, so a never-registered green is still caught)."""
    import os as _os
    import sqlite3 as _sq
    if not green_sid:
        return None
    db = _os.path.join(_os.path.expanduser("~/.gemini/antigravity-cli/conversations"),
                       f"{green_sid}.db")
    if not _os.path.exists(db):
        return None
    try:
        c = _sq.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(c.execute("SELECT COUNT(*) FROM steps").fetchone()[0])
        finally:
            c.close()
    except Exception:  # noqa: BLE001 — fail-soft
        return None


def green_progress_codex(green_sid):
    """Codex green-progress (mirrors green_progress_gemini): the green ROLLOUT's task_started
    count — the monotonic turn high-water as the codex green works. Codex has no sqlite steps
    table; the store is ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl (one line per
    event; ``"type":"task_started"`` = one per turn). Locate by sid; absent/unreadable => None
    (caller treats None as no-progress => a never-registered green is still caught)."""
    import glob as _glob
    import os as _os
    if not green_sid:
        return None
    import json as _json
    hits = _glob.glob(_os.path.expanduser(
        f"~/.codex/sessions/*/*/*/rollout-*-{green_sid}.jsonl"))
    if not hits:
        return None
    try:
        n = 0
        with open(hits[0], errors="ignore") as fh:
            for line in fh:
                # a fast pre-filter, then parse to avoid matching the substring inside
                # other payloads / whitespace-variant JSON (codex writes compact; a
                # fixture may not).
                if "task_started" not in line:
                    continue
                try:
                    d = _json.loads(line)
                except ValueError:
                    continue
                if (d.get("payload") or {}).get("type") == "task_started":
                    n += 1
        return n
    except OSError:  # fail-soft
        return None


GREEN_PROGRESS_REGISTRY = {
    "gemini": green_progress_gemini,
    "codex": green_progress_codex,
}


def resolve_codex_cid(seat, *, retired_sids_fn=None):
    """Resolve a codex seat's session id (sid) by EFFECT (mirrors resolve_gemini_cid): the
    rollout whose FIRST user message declares ``You are <seat>``. Codex rollouts live at
    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl; the spawn's inject_prompt lands as a
    ``response_item`` / ``payload.type=='message'`` / ``role=='user'`` with input_text content.
    The sid is the trailing UUID in the filename (== session_meta payload.session_id). Newest
    rollout first; only the FIRST user message declares identity. Read-only; returns sid|None."""
    import glob as _glob
    import json as _json
    import os as _os
    import re as _re
    root = _os.path.expanduser("~/.codex/sessions")
    # gen-suffix tolerant (like the claude candidate scan): a promoted green declares
    # 'You are <seat>-g<N>' / '-gen<N>' and IS the seat; any other suffix is another seat.
    pat = _re.compile(rf"you are {_re.escape(seat)}(?:-g(?:en)?\d+)?(?![\w-])")
    sid_re = _re.compile(r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$")
    # DB-retired generations of this root are NEVER candidates (gm msg_faf46471): a reaped
    # or parked predecessor's rollout can carry the newest mtime and would win here.
    retired = _retired_set(seat, retired_sids_fn, "resolve_codex_cid")
    for p in sorted(_glob.glob(f"{root}/*/*/*/rollout-*.jsonl"),
                    key=_os.path.getmtime, reverse=True):
        if retired:
            _m = sid_re.search(_os.path.basename(p))
            if _m and _m.group(1) in retired:
                continue
        try:
            with open(p, errors="ignore") as fh:
                seen_user = 0
                for line in fh:
                    try:
                        d = _json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") != "response_item":
                        continue
                    pay = d.get("payload") or {}
                    if pay.get("type") != "message" or pay.get("role") != "user":
                        continue
                    txt = " ".join(c.get("text", "") for c in (pay.get("content") or [])
                                   if isinstance(c, dict)).lower()
                    if pat.search(txt):
                        m = sid_re.search(_os.path.basename(p))
                        return m.group(1) if m else None
                    # Codex injects an <environment_context> user message BEFORE the
                    # identity declaration (unlike gemini, where identity IS the first
                    # user input). Scan the short boot burst, not just the first user msg,
                    # but bound it so we never match a later conversational echo.
                    seen_user += 1
                    if seen_user >= 5:
                        break
        except OSError:
            continue
    return None


# CID resolver registry (a): runtime -> resolve the seat's LIVE conversation id by effect.
# Same DATA-lookup discipline as CTX_ADAPTER_REGISTRY; the autonomous beat resolves a green's
# cid provider-agnostically (resolve_cid_any tries each until one hits off its own store).
def _claude_proc_info(seat):
    """Live default (a): the seat's tmux pane -> its `claude` descendant process, with cmdline,
    cwd, and process start epoch. None if no live claude process. Read-only."""
    import subprocess as _sp, os as _os
    r = _sp.run(["tmux", "list-panes", "-t", seat, "-F", "#{pane_pid}"],
                capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.split():
        return None
    pane_pid = int(r.stdout.split()[0])
    # BFS the pane's process subtree for a `claude` binary
    frontier, claude_pid, depth = [pane_pid], None, 0
    while frontier and depth < 6 and claude_pid is None:
        nxt = []
        for pid in frontier:
            try:
                cl = open(f"/proc/{pid}/cmdline").read().replace("\0", " ")
            except OSError:
                continue
            if "/claude" in cl or cl.strip().startswith("claude") or " claude " in cl:
                claude_pid = pid
                break
            ch = _sp.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
            nxt += [int(x) for x in ch.stdout.split()]
        frontier, depth = nxt, depth + 1
    if claude_pid is None:
        return None
    try:
        cmdline = open(f"/proc/{claude_pid}/cmdline").read().replace("\0", " ")
        cwd = _os.readlink(f"/proc/{claude_pid}/cwd")
    except OSError:
        return None
    return {"cmdline": cmdline, "cwd": cwd, "start_epoch": _proc_start_epoch(claude_pid),
            "pid": claude_pid}


def _proc_start_epoch(pid):
    """Process start as a unix epoch: btime (/proc/stat) + starttime_ticks (/proc/<pid>/stat
    field 22) / CLK_TCK. None on any read error (verify then fails-closed)."""
    import os as _os
    try:
        btime = None
        with open("/proc/stat") as fh:
            for line in fh:
                if line.startswith("btime "):
                    btime = int(line.split()[1]); break
        with open(f"/proc/{pid}/stat") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()   # after "comm)" to survive spaces in comm
        starttime = int(fields[19])                         # field 22 overall = index 19 post-")"
        clk = _os.sysconf("SC_CLK_TCK")
        return btime + starttime / clk if btime else None
    except (OSError, ValueError, IndexError):
        return None


def _claude_transcript_candidates(seat, cwd, *, projects_root=None, encode_fn=None,
                                  boot_user_msgs=6):
    """Live default (b): the seat's own transcript(s) whose BOOT BURST (first ``boot_user_msgs``
    user turns) declares ``You are <seat>`` or ``agent-init-<seat>`` — gen-suffix tolerant
    (``<seat>-gN``), so a canonical query resolves a rotated worker's gen-suffixed identity. A
    later conversational echo (past the boot bound) never matches. Returns [(sid, mtime)]."""
    import glob as _glob, json as _json, os as _os, re as _re
    root = projects_root or _os.path.expanduser("~/.claude/projects")
    enc = (encode_fn or (lambda c: _re.sub(r"[/.]", "-", c)))(cwd)
    proj = _os.path.join(root, enc)
    pat = _re.compile(rf"(?:you are {_re.escape(seat)}|agent-init-{_re.escape(seat)})"
                      rf"(?:-g\d+)?(?![\w-])")
    out = []
    for p in sorted(_glob.glob(f"{proj}/*.jsonl"), key=_os.path.getmtime, reverse=True):
        try:
            seen_user = 0
            with open(p, errors="ignore") as fh:
                for line in fh:
                    try:
                        d = _json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") != "user":
                        continue
                    msg = d.get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content
                                           if isinstance(c, dict))
                    if pat.search(str(content or "").lower()):
                        out.append((_os.path.basename(p)[:-6], _os.path.getmtime(p)))
                        break
                    seen_user += 1
                    if seen_user >= boot_user_msgs:
                        break
        except OSError:
            continue
    return out


def _claude_verify_fresh(sid, mtime, start_epoch):
    """Live default (c): the candidate is THIS instance's live transcript iff it was appended at
    or after the process start (a live append) — a stale prior-gen transcript's mtime predates
    the start. Fallback: a fresh /tmp/claude-ctx-<sid>.json detector. Fail-closed on no signal."""
    import os as _os
    if start_epoch is None:
        # no process start to compare — require a detector that exists (weak but not stale-blind)
        return _os.path.exists(f"/tmp/claude-ctx-{sid}.json")
    if mtime is not None and mtime >= start_epoch - 5:
        return True
    try:
        return _os.path.getmtime(f"/tmp/claude-ctx-{sid}.json") >= start_epoch - 5
    except OSError:
        return False


# A live claude session appends to exactly ONE transcript; the live one is the newest-appended
# boot-burst match. Two boot-burst matches within this window are a genuine ambiguity (fail-closed).
CLAUDE_CID_TIE_WINDOW_S = 120.0


def _retired_sids_for_seat(seat):
    """Sids the write-truth DB holds as RETIRED generations of the seat's ROOT (gm msg_1074aa87).
    A rename-not-kill predecessor pane (<root>-genN) keeps appending to its own transcript, so
    the newest-mtime tie-break can pick a retired generation's sid over the live canonical's.
    Those sids are never candidates. Alias seats (<root>-gN / <root>-genN) resolve to the root.
    Read-only; any DB problem => empty set (the caller keeps its previous behaviour)."""
    import os as _os
    import re as _re
    import sqlite3 as _sqlite3
    root = _re.sub(r"-g(?:en)?\d+$", "", seat or "")
    if not root:
        return set()
    orch = _os.environ.get("ORCHESTRA_DIR") or _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    db = _os.path.join(orch, "state", "orchestra-registry.db")
    con = _sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT session_id FROM generations WHERE root=? AND retired_at IS NOT NULL "
            "AND session_id IS NOT NULL", (root,)).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows if r[0]}


_claude_retired_sids = _retired_sids_for_seat   # historical name (4129e2ef06)


def _retired_set(seat, retired_sids_fn, who):
    """Shared: the seat root's DB-retired sids, fail-soft (an unreadable DB => empty set)."""
    try:
        return set((retired_sids_fn or _retired_sids_for_seat)(seat) or ())
    except Exception as e:  # noqa: BLE001 — never let the exclusion break resolution
        print(f"[{who}] {seat}: retired-sid lookup failed ({e!r}); proceeding without "
              f"exclusion", flush=True)
        return set()


def resolve_claude_cid(seat, *, proc_fn=None, candidates_fn=None, verify_fn=None,
                       retired_sids_fn=None):
    """Resolve a CLAUDE seat's live session id (sid) by EFFECT (broad-arm blocker fix). 3 sources
    in order: (1) PROCESS-FIRST — the seat's live `claude ... --resume <sid>` cmdline is
    AUTHORITATIVE (fresh spawns carry --settings but no --resume -> fall through); (2) TRANSCRIPT
    STORE — the seat's own boot-burst transcript declaring ``You are <seat>`` (gen-suffix tolerant),
    newest-first; (3) VERIFY — the candidate's transcript/detector mtime must post-date the process
    start (never a stale prior-gen sid, the wrong-transcript landmine). Ambiguous (>1 verified) or
    none => None + LOUD reason; never guess. Read-only; mirrors resolve_gemini_cid/resolve_codex_cid."""
    import re as _re
    proc_fn = proc_fn or _claude_proc_info
    candidates_fn = candidates_fn or _claude_transcript_candidates
    verify_fn = verify_fn or _claude_verify_fresh
    info = proc_fn(seat)
    if not info:
        print(f"[resolve_claude_cid] {seat}: no live claude process for pane (unresolvable)",
              flush=True)
        return None
    m = _re.search(r"--resume\s+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", info.get("cmdline", ""))
    if m:
        return m.group(1)   # SOURCE 1 authoritative
    start = info.get("start_epoch")
    cands = candidates_fn(seat, info.get("cwd"))
    # DB-retired generations of this root are NEVER candidates (gm msg_1074aa87): a parked
    # rename-not-kill predecessor still appends to its transcript and would win newest-mtime.
    # Fail-soft: an unreadable DB leaves the candidate set as before.
    retired = _retired_set(seat, retired_sids_fn, "resolve_claude_cid")
    if retired:
        dropped = [sid for (sid, _mt) in cands if sid in retired]
        if dropped:
            print(f"[resolve_claude_cid] {seat}: excluding {len(dropped)} DB-retired "
                  f"generation sid(s) e.g. {[d[:8] for d in dropped[:3]]}", flush=True)
        cands = [(sid, mt) for (sid, mt) in cands if sid not in retired]
    # SOURCE 3: verify freshness per candidate, then the LIVE session is the NEWEST-appended
    # boot-burst match. A superseded early transcript (abandoned after a compaction/restart within
    # a LONG-running process) also post-dates the ancient process start, so ">= start" alone is not
    # enough for long-lived seats (verified by effect: pm-molevera's live transcript was ~11 days
    # newer than an abandoned 17-min early one). Newest-appended wins; a genuine near-tie
    # (both appended within CLAUDE_CID_TIE_WINDOW_S) is fail-closed — never guess between two live.
    verified = sorted(((sid, mt) for (sid, mt) in cands if verify_fn(sid, mt, start)),
                      key=lambda x: x[1], reverse=True)
    if not verified:
        print(f"[resolve_claude_cid] {seat}: no verified live transcript "
              f"(candidates={len(cands)}, start={start}) — fail-closed", flush=True)
        return None
    if len(verified) >= 2 and (verified[0][1] - verified[1][1]) < CLAUDE_CID_TIE_WINDOW_S:
        print(f"[resolve_claude_cid] {seat}: AMBIGUOUS near-tie "
              f"{[v[0] for v in verified[:2]]} (mtime delta "
              f"{verified[0][1] - verified[1][1]:.0f}s < {CLAUDE_CID_TIE_WINDOW_S}s) — fail-closed",
              flush=True)
        return None
    return verified[0][0]


CID_RESOLVER_REGISTRY = {
    "claude": resolve_claude_cid,
    "gemini": resolve_gemini_cid,
    "codex": resolve_codex_cid,
}


def green_progress(runtime, green_sid):
    """Registry entry point (b): the GREEN's own progress high-water, keyed on its sid. Dict
    lookup on the runtime; unknown runtime / any error / unresolved sid => None (fail-closed:
    the stall bound then accrues, so a green that never registered its sid is caught)."""
    fn = GREEN_PROGRESS_REGISTRY.get((runtime or "").strip().lower())
    if fn is None:
        return None
    try:
        return fn(green_sid)
    except Exception:  # noqa: BLE001 — fail-soft: never raise into the beat
        return None


def green_progress_any(green_sid):
    """(b) provider-agnostic: the green's progress high-water WITHOUT knowing its runtime —
    try each registered resolver; only the runtime whose store actually holds that sid
    returns a value (the others fail-soft to None). Lets the autonomous beat measure green
    progress without threading a runtime. None if no resolver recognizes the sid."""
    if not green_sid:
        return None
    for fn in GREEN_PROGRESS_REGISTRY.values():
        try:
            v = fn(green_sid)
        except Exception:  # noqa: BLE001 — fail-soft
            v = None
        if v is not None:
            return v
    return None


def resolve_cid_any(seat):
    """(a) provider-agnostic: resolve a seat's LIVE cid WITHOUT knowing its runtime — try
    each registered resolver, return the first hit. Only the seat's own runtime store
    declares it, so at most one resolver returns non-None. None if unresolvable."""
    if not seat:
        return None
    for fn in CID_RESOLVER_REGISTRY.values():
        try:
            cid = fn(seat)
        except Exception:  # noqa: BLE001 — fail-soft
            cid = None
        if cid:
            return cid
    return None


# ---- WAL source-path registry: runtime -> the capture source for a cid ----
# The one genuinely runtime-specific input the multiplexer's make_adapter needs: WHERE the
# provider writes its live transcript. A DATA lookup (same discipline as the other registries)
# so a runtime-dispatched capture stays free of runtime-name conditionals in its control flow.
#   gemini -> ~/.gemini/antigravity-cli/conversations/<cid>.db   (sqlite; GeminiWalAdapter)
#   codex  -> ~/.codex/sessions/**/rollout-*-<cid>.jsonl         (jsonl;   CodexWalAdapter)
#   claude -> ~/.claude/projects/<proj_slug(cwd)>/<sid>.jsonl    (jsonl;   ClaudeWalAdapter)
def _source_path_gemini(cid, cwd=None):
    import os as _os
    return _os.path.join(_os.path.expanduser("~/.gemini/antigravity-cli/conversations"),
                         f"{cid}.db")


def _source_path_codex(cid, cwd=None):
    import glob as _glob
    import os as _os
    hits = _glob.glob(_os.path.expanduser(
        f"~/.codex/sessions/*/*/*/rollout-*-{cid}.jsonl"))
    return hits[0] if hits else None


def _source_path_claude(cid, cwd=None):
    import os as _os
    slug = (cwd or "").replace("/", "-")
    return _os.path.join(_os.path.expanduser("~/.claude/projects"), slug, f"{cid}.jsonl")


SOURCE_PATH_REGISTRY = {
    "gemini": _source_path_gemini,
    "codex": _source_path_codex,
    "claude": _source_path_claude,
}


def source_path_for(runtime, cid, cwd=None):
    """The live capture source path for `cid` under `runtime` (data lookup; unknown runtime
    or missing cid => None). Feeds the multiplexer make_adapter(runtime).tail(source_path)."""
    fn = SOURCE_PATH_REGISTRY.get((runtime or "").strip().lower())
    if fn is None or not cid:
        return None
    try:
        return fn(cid, cwd)
    except Exception:  # noqa: BLE001 — fail-soft
        return None
