#!/usr/bin/env python3
"""Agent-facing approval CLI. Usage:
  approval.py request "<question>" --from <agent> --worker-kind pane|node [--op-key K] [--thread-key T] [--options approve,deny,hold]
  approval.py get <id>
  approval.py ack <id> --from <agent>
On 'request' the agent should then PARK (end its turn) — it will be resumed when the operator answers."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore
from approval_notify import notify

def _human_task_title(task, delegated_by, feature):
    """The visible task line. Infra human_task keeps the '(delegated by X)'
    provenance prefix; a commitment card drops it (feature=='commitment') so
    the operator's card leads with the task — provenance stays in from_agent/delegated_by
    (contract DEC-1789349623024244). The delegated-mint AUTHORITY is unchanged."""
    if delegated_by and feature != "commitment":
        return f"(delegated by {delegated_by}) {task}"
    return task


def _lint_commitment_feature(store):
    """Reviewer amendment D (DEC-1789349623024244): no PENDING row may carry
    feature='commitment' unless its op_key is a cmt_ commitment id. Returns the
    offending row ids (empty = clean)."""
    import sqlite3
    c = sqlite3.connect(store.db_path); c.row_factory = sqlite3.Row
    try:
        rows = c.execute("SELECT id, op_key FROM approval_requests "
                         "WHERE status='pending' AND feature='commitment'").fetchall()
        return [r["id"] for r in rows if not str(r["op_key"] or "").startswith("cmt_")]
    finally:
        c.close()


def _store():
    # APPROVAL_DB_PATH env override (mirrors ORCHESTRA_DIR / REGISTRY_PATH): lets a
    # provider ADAPTER or a hermetic test drive the canonical service against a
    # SCRATCH db without importing ApprovalStore (the operator's service-boundary ruling —
    # one service owns the store, N thin callers reach it only through this CLI).
    # Production sets no env and writes the live tasks.db exactly as before.
    return ApprovalStore(db_path=os.environ.get("APPROVAL_DB_PATH") or None)


# --- Direct-decision-routing interposer guard (DEC-1787724456, the operator's direct-
# edge ruling 2026-08-25 + routing-deviation audit D1/I1). A decision card is a
# two-party edge: the agent that NEEDS the decision authors it (--from itself)
# so the operator's answer resumes IT. This guard enforces authorship at the single
# card producer: a card may never be authored --from a mere interposer.
# `--delegated-by <agent>` is PROVENANCE ONLY (records that an authority
# carries a decision originally raised by another agent) — it is NEVER a
# bypass: the pass condition stays strictly canonical(caller)==canonical(--from).
# FAIL-OPEN on zero signal (no tmux, unknown/ambiguous session, cron/node
# callers, unreadable registry): a guard must never strand a legitimate
# decision — worst case is today's behavior plus a stderr warning.

def _canon(name):
    """Canonical seat name: strip a -genN rotation-alias suffix."""
    import re
    return re.sub(r"-gen\d+$", "", (name or "").strip())


def _caller_identities():
    """Resolve the CALLING process's seat identities from its tmux pane.

    Returns a set of acceptable identity strings (registry id/name/
    tmux_session/lineage_root of the ONE online registry row matching this
    pane's tmux session), or None on zero/ambiguous signal (=> fail-open).
    Only status=online rows count — retired/residue/service rows are
    zero-signal by ruling (reviewer note, DEC-1787724456)."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        import subprocess
        out = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#S"],
                             capture_output=True, text=True, timeout=3)
        session = (out.stdout or "").strip()
        if out.returncode != 0 or not session:
            return None
    except Exception:  # noqa: BLE001 — no tmux server etc. => zero signal
        return None
    reg_path = os.environ.get("REGISTRY_PATH") or os.path.join(
        os.environ.get("ORCHESTRA_DIR")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "registry.json")
    try:
        with open(reg_path) as f:
            reg = json.load(f)
    except Exception:  # noqa: BLE001 — unreadable registry => zero signal
        return None
    # registry `agents` is a DICT keyed by agent id in the live file (list
    # accepted for scratch/test registries) — normalize to (key, row) pairs.
    # Dict-shape bug found live 2026-08-26 (guard was permanently fail-open).
    agents = reg.get("agents") or {}
    if isinstance(agents, dict):
        pairs = [(k, v) for k, v in agents.items() if isinstance(v, dict)]
    else:
        pairs = [(None, v) for v in agents if isinstance(v, dict)]
    live = [(k, a) for k, a in pairs
            if a.get("status") == "online"
            and a.get("tmux_session") == session]
    if len(live) != 1:
        return None  # zero or ambiguous — never guess
    key, row = live[0]
    idents = {_canon(str(v)) for v in (key, row.get("id"), row.get("name"),
                                       row.get("tmux_session"),
                                       row.get("lineage_root")) if v}
    return idents or None


def _authorship_guard(from_agent):
    """True = proceed. False = REFUSE (caller is provably authoring a card
    for a different agent — the interposer anti-pattern)."""
    idents = _caller_identities()
    if idents is None:
        print("[approval] authorship-guard: no caller signal (non-tmux/cron/"
              f"unknown session) — fail-open, from={from_agent}", file=sys.stderr)
        return True
    if _canon(from_agent) in idents:
        return True
    caller = sorted(idents)[0]
    print(f"[approval] REFUSED (interposer guard): you are '{caller}' but "
          f"authoring --from '{from_agent}'. A decision card is a two-party "
          f"edge — the agent that NEEDS the decision authors it so the operator's "
          f"answer resumes IT. Either (a) have '{from_agent}' fire this card "
          f"itself, or (b) if you hold delegated authority over this decision, "
          f"author it as YOURSELF: --from {caller} --delegated-by {from_agent}. "
          f"Wording review stays upstream (draft via msg_store); never fire "
          f"another agent's card.", file=sys.stderr)
    return False


def _stamp_delegation(summary, delegated_by):
    """Prepend delegation provenance to the card's plain-English summary."""
    if not delegated_by:
        return summary
    line = f"**Delegated by:** {delegated_by} — this authority carries the decision; the answer resumes the card author."
    return f"{line}\n\n{summary}" if summary else line


def _qstore():
    # SAME scratch override as _store(): the questionnaire container must honor
    # APPROVAL_DB_PATH or a hermetic test's questionnaire WRITES THE LIVE
    # SURFACE (leak found by gm 2026-08-26: test qnr rows reached the operator's feed).
    from questionnaire_schema import QuestionnaireStore
    return QuestionnaireStore(db_path=os.environ.get("APPROVAL_DB_PATH") or None)

def _expiry_notice(row):
    """One durable msg_store row to the card's AUTHOR when its pending decision expired
    (gm msg_86bcc168 item 4: apr_79e73ed4 sat 5 days with nobody told). Raises on failure;
    expire_due records notified=False and continues."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from msg_store import MessageStore
    summary = (row.get("summary") or row.get("question") or "")[:160]
    MessageStore().send(
        from_agent="approval-loop", to_agent=row["from_agent"], type="approval_expired",
        priority="medium", source="approval-ttl",
        subject=f"your pending decision {row['id']} EXPIRED unanswered (TTL sweep)",
        body=(f"Your approval/decision card {row['id']} (created {row.get('created_at')}, "
              f"expires_at {row.get('expires_at')}) was still pending past its TTL and has been "
              f"set to status=expired by the approvals TTL sweep. Summary: {summary!r}. If the "
              f"decision is still needed, re-issue a FRESH card (surface-decision skill) — do not "
              f"assume the operator saw this one."),
        allow_unaddressable=True)
    return True


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("request")
    pr.add_argument("question")
    pr.add_argument("--from", dest="from_agent", required=True)
    pr.add_argument("--worker-kind", choices=["pane", "node"], required=True)
    pr.add_argument("--op-key", default=None)
    pr.add_argument("--thread-key", default=None)
    pr.add_argument("--options", default=None, help="comma-separated; default approve,deny,hold")
    pr.add_argument("--summary", default=None, help="markdown 'simple explanation' (expandable detail)")
    pr.add_argument("--risk", dest="risk_level", default=None, choices=["low", "medium", "high"])
    pr.add_argument("--reversibility", default=None, choices=["easy", "hard", "irreversible"])
    pr.add_argument("--feature", default=None, help="product area/feature, e.g. 'Proposals', 'Billing'")
    pr.add_argument("--menu-json", default=None,
                    help="JSON pane-menu capture {question, options[{n,label,detail?,input_kind}], "
                         "selected_n, source_session, captured_at} -> kind='menu' row "
                         "(menu-bridge + decision-surface fixtures)")
    # --- Provider-neutral IDENTITY pass-through (spec §1.1/§4, R4) ---
    # The ONE canonical service accepts the identity fields a provider adapter
    # CAPTURES from its runtime (seat_id/run_id/generation/seat_epoch from the
    # registry row + cv4, NEVER self-declared by the model; provider +
    # provider_session_id + process_instance_id native to the runtime). `provider`
    # is stored as PROVENANCE only — no code path below branches on it. All
    # nullable: an omitted flag reproduces today's legacy-row behavior exactly.
    pr.add_argument("--seat-id", dest="seat_id", default=None)
    pr.add_argument("--run-id", dest="run_id", default=None)
    pr.add_argument("--generation", type=int, default=None)
    pr.add_argument("--seat-epoch", dest="seat_epoch", type=int, default=None)
    pr.add_argument("--provider", default=None,
                    choices=["claude", "gemini", "codex"],
                    help="PROVENANCE only — never a state-machine branch")
    pr.add_argument("--provider-session-id", dest="provider_session_id", default=None)
    pr.add_argument("--process-instance-id", dest="process_instance_id", default=None)
    pr.add_argument("--process-lease-id", dest="process_lease_id", default=None)
    pr.add_argument("--origin", default=None,
                    choices=["canonical", "backfill_fs", "shadow_proposal"])
    pr.add_argument("--delegated-by", dest="delegated_by", default=None,
                    help="PROVENANCE only (DEC-1787724456): the agent that "
                         "originally needed this decision, when YOU (the card "
                         "author) carry it as its delegated authority. Never "
                         "a bypass — --from must still be YOU.")
    # Evidence-attached completion brief (spec §3.4 — the anti-"claiming done").
    # Agent reports DONE with proof; the operator accepts (archive) or responds (follow-up).
    pc = sub.add_parser("complete")
    pc.add_argument("question", help="short completion title, e.g. 'Voice panel live E2E'")
    pc.add_argument("--from", dest="from_agent", required=True)
    pc.add_argument("--summary", default=None, help="markdown brief of what shipped")
    pc.add_argument("--feature", default=None)
    pc.add_argument("--thread-key", default=None)
    pc.add_argument("--evidence-test", default=None,
                    help="test/verification output (verbatim text)")
    pc.add_argument("--evidence-url", action="append", default=[],
                    help="repeatable; 'Label=https://…' or bare URL (deploy, screenshot)")
    pc.add_argument("--verified", action="store_true",
                    help="evidence was RUN and confirmed (else badge shows REPORTED)")
    pc.add_argument("--delegated-by", dest="delegated_by", default=None,
                    help="PROVENANCE only (DEC-1787724456); --from must be YOU")
    # "Waiting on you" human-blocker (SERVER spec 2026-08-25 §6). A human_task
    # exists ONLY for an OFFLINE action the operator must take that is NOT already a card.
    # --op-key is REQUIRED (Q4 idempotency + no-spam). --blocks is optional: a
    # free-text label, OR an existing apr_/qnr_ id (deep-link + dedup key). A
    # PENDING referenced apr_/qnr_, or any perm:, REFUSES the post (no double-card).
    ph = sub.add_parser("human-task")
    ph.add_argument("--from", dest="from_agent", required=True)
    ph.add_argument("--task", required=True, help="what the operator must do (markdown)")
    ph.add_argument("--op-key", dest="op_key", required=True,
                    help="REQUIRED idempotency key (Q4)")
    ph.add_argument("--blocks", default=None,
                    help="what/who it unblocks: a label OR an apr_/qnr_ id (deep-link + dedup)")
    ph.add_argument("--feature", default=None)
    ph.add_argument("--summary", default=None,
                    help="markdown subtitle/context (e.g. commitment 3-line **Task:**/**You said:**/**From:**)")
    ph.add_argument("--evidence", default=None,
                    help="JSON evidence col; on commitment rows {\"due_ts\":ISO8601,\"due_source\":str} for the countdown chip (DEC-1789352893701528)")
    ph.add_argument("--worker-kind", dest="worker_kind", choices=["pane", "node"],
                    default="pane")
    ph.add_argument("--delegated-by", dest="delegated_by", default=None,
                    help="PROVENANCE only (DEC-1787724456); --from must be YOU")
    pt = sub.add_parser("patch")  # additive in-place update (contract DEC-1789349623024244)
    pt.add_argument("--id", required=True)
    pt.add_argument("--from", dest="from_agent", required=True)
    pt.add_argument("--feature", default=None)
    pt.add_argument("--block-task", dest="block_task", default=None)
    pt.add_argument("--blocks-what", dest="blocks_what", default=None)
    pt.add_argument("--summary", default=None)
    pt.add_argument("--evidence", default=None,
                    help="JSON evidence col; commitment due_ts backfill {\"due_ts\":ISO8601,\"due_source\":str} (DEC-1789352893701528)")
    pg = sub.add_parser("get"); pg.add_argument("id")
    pa = sub.add_parser("ack"); pa.add_argument("id"); pa.add_argument("--from", dest="from_agent", required=True)
    # Questionnaire = CONTAINER of N grouped questions (spec §5). The agent PARKS
    # after emitting, exactly like `request`; delivered as ONE batch on submit.
    pq = sub.add_parser("questionnaire")
    pq.add_argument("--from", dest="from_agent", required=True)
    pq.add_argument("--title", required=True)
    pq.add_argument("--summary", default=None)
    pq.add_argument("--feature", default=None)
    pq.add_argument("--thread-key", default=None)
    pq.add_argument("--op-key", default=None)
    pq.add_argument("--worker-kind", choices=["pane", "node"], default="pane")
    pq.add_argument("--urgency", type=int, default=0, help="0-3 priority input (spec §3.1)")
    pq.add_argument("--questions-json", required=True,
                    help="JSON list [{prompt, kind:'menu'|'free_text', menu?:{options:[...]}}] or @file")
    pq.add_argument("--delegated-by", dest="delegated_by", default=None,
                    help="PROVENANCE only (DEC-1787724456); --from must be YOU")
    pk = sub.add_parser("qack"); pk.add_argument("id")
    pk.add_argument("--from", dest="from_agent", required=True)
    # --- Web-convergence transports (SPEC all-model-parity R5) ---
    # The rewritten web API (unified-approvals.ts / approvals.ts) is a THIN
    # caller of THIS one canonical service (§4 service boundary — N callers,
    # one ApprovalStore writer). It never imports the store or mutates the DB
    # directly; it shells to these two subcommands so the answer transition and
    # the pending feed come from the SAME state machine iOS/watch use.
    #   pending --json : the canonical pending feed the web dashboard reads
    #   answer         : land a web answer through watch_gateway.apply_answer
    pes = sub.add_parser("expire-sweep",
                         help="list (default, dry-run) or --apply expire PENDING rows past expires_at, "
                              "telling each author via msg_store; inert unless EXPIRE_PENDING=1 "
                              "(the operator 2026-08-13 apr_10c0c829: nothing expires)")
    pes.add_argument("--apply", action="store_true",
                     help="actually set status=expired + notify authors (default is dry-run)")
    pp = sub.add_parser("pending")
    pp.add_argument("--json", action="store_true",
                    help="emit the canonical pending rows as a JSON list")
    pan = sub.add_parser("answer")
    pan.add_argument("--id", dest="rid", required=True)
    pan.add_argument("--answer", required=True,
                     help="approve|deny|hold|accept|respond|option (validated by the ONE core)")
    pan.add_argument("--text", default=None, help="free-text note (e.g. a deny reason)")
    pan.add_argument("--option-n", dest="option_n", type=str, default=None,
                     help="menu rows only: the chosen option index as a "
                          "stringified 1-based digit, e.g. '2' (the ONE core "
                          "rejects a non-string option_n by contract)")
    pan.add_argument("--answer-text", dest="answer_text", default=None,
                     help="menu free-text answer")
    # R7d attribution tags (provenance-only). The web bridge (_canonical-approvals)
    # shells this same verb with `--surface web --answered-by shaw`; a bare agent
    # self-ack defaults to the self-authored 'agent_cli' edge.
    pan.add_argument("--surface", default="agent_cli",
                     help="answering surface: web|phone|watch|gateway|agent_cli")
    pan.add_argument("--answered-by", dest="answered_by", default=None,
                     help="principal: 'operator' (authenticated edges) | '<agent_id>' "
                          "(self-authored gates)")
    args = p.parse_args(argv)
    store = _store(); store.migrate()

    if args.cmd == "request":
        if not _authorship_guard(args.from_agent):
            return 4
        opts = None
        if args.options:
            raw_opts = args.options.strip()
            if raw_opts.startswith("["):
                try:
                    parsed = json.loads(raw_opts)
                    if isinstance(parsed, list):
                        opts = [str(o).strip() for o in parsed if str(o).strip()]
                except Exception:
                    opts = None
            if opts is None:
                opts = [o.strip() for o in raw_opts.split(",") if o.strip()]
            if not opts:
                opts = None

        menu, kind = None, None
        if args.menu_json:
            try:
                menu = json.loads(args.menu_json)
                if not isinstance(menu, dict) or not isinstance(menu.get("options"), list):
                    raise ValueError("menu must be an object with an options list")
            except ValueError as e:
                print(f"[approval] bad --menu-json: {e}", file=sys.stderr)
                return 2
            kind = "menu"
            if opts is None:   # legacy renderers see the labels
                opts = [str(o.get("label", "")) for o in menu["options"] if isinstance(o, dict)]
        elif opts and opts not in (["approve", "deny", "hold"], ["approve", "deny"]):
            # Auto-synthesize menu object for custom options so Watch / iOS client render option buttons
            kind = "menu"
            free_text_classes = ("type something", "write-in", "write in", "custom answer", "other")
            menu_opts = []
            has_free_text = False
            for i, opt in enumerate(opts):
                lbl = str(opt).strip().rstrip(".").lower()
                is_ft = any(lbl.startswith(c) for c in free_text_classes) or lbl in free_text_classes
                if is_ft:
                    has_free_text = True
                menu_opts.append({"n": str(i + 1), "label": opt, "input_kind": "free_text" if is_ft else "direct"})

            if not has_free_text:
                menu_opts.append({"n": str(len(menu_opts) + 1), "label": "Other / Write-in...", "input_kind": "free_text"})
                opts.append("Other / Write-in...")

            menu = {
                "question": args.question,
                "options": menu_opts,
            }
        rid = store.create(from_agent=args.from_agent, question=args.question,
                           worker_kind=args.worker_kind, op_key=args.op_key,
                           thread_key=args.thread_key, options=opts,
                           summary=_stamp_delegation(args.summary, args.delegated_by),
                           risk_level=args.risk_level,
                           reversibility=args.reversibility, feature=args.feature,
                           kind=kind, menu=menu,
                           seat_id=args.seat_id, run_id=args.run_id,
                           generation=args.generation, seat_epoch=args.seat_epoch,
                           provider=args.provider,
                           provider_session_id=args.provider_session_id,
                           process_instance_id=args.process_instance_id,
                           process_lease_id=args.process_lease_id,
                           origin=args.origin)
        try:
            notify(rid, store=store)            # primary trigger: instant push
        except Exception as e:  # noqa: BLE001 — cron backstop will re-notify
            print(f"[approval] notify deferred to backstop: {e}", file=sys.stderr)
        print(rid)
        return 0
    if args.cmd == "complete":
        if not _authorship_guard(args.from_agent):
            return 4
        urls = []
        for u in args.evidence_url:
            label, _, url = u.partition("=")
            if url:
                urls.append({"label": label.strip(), "url": url.strip()})
            else:
                urls.append({"label": "Link", "url": u.strip()})
        evidence = {"tests": args.evidence_test, "urls": urls, "verified": bool(args.verified)}
        rid = store.create(from_agent=args.from_agent, question=args.question,
                           worker_kind="pane", thread_key=args.thread_key,
                           options=["accept", "respond"],
                           summary=_stamp_delegation(args.summary, args.delegated_by),
                           feature=args.feature,
                           kind="completion", evidence=evidence)
        try:
            notify(rid, store=store)
        except Exception as e:  # noqa: BLE001 — cron backstop will re-notify
            print(f"[approval] notify deferred to backstop: {e}", file=sys.stderr)
        print(rid)
        return 0
    if args.cmd == "human-task":
        if not _authorship_guard(args.from_agent):
            return 4
        # §4 dedup: resolve --blocks. A PENDING apr_/qnr_ means "you're already
        # carded" -> REFUSE (no double-card); a perm: means "already carded by a
        # live permission prompt" -> REFUSE; a resolved/unknown id or any other
        # value is a context-only label -> allow.
        blocks = args.blocks
        if blocks:
            if blocks.startswith("perm:"):
                print(f"[approval] refused: {blocks} — you're already carded by a live "
                      f"permission prompt; answer it in your pane (/agent-key).",
                      file=sys.stderr)
                return 3
            if blocks.startswith("apr_"):
                ref = store.get(blocks)
                if ref is not None and ref.get("status") == "pending":
                    print(f"[approval] refused: {blocks} is still pending — you are "
                          f"already the surface; park on it (no double-card).",
                          file=sys.stderr)
                    return 3
            elif blocks.startswith("qnr_"):
                qref = _qstore().get(blocks)
                if qref is not None and qref.get("status") == "pending":
                    print(f"[approval] refused: {blocks} is still pending — you are "
                          f"already the surface; park on it (no double-card).",
                          file=sys.stderr)
                    return 3
        task_md = _human_task_title(args.task, args.delegated_by, args.feature)
        rid = store.create(from_agent=args.from_agent, question=task_md,
                           worker_kind=args.worker_kind, op_key=args.op_key,
                           options=["done", "cant", "snooze"], feature=args.feature,
                           kind="human_task", block_task=task_md, blocks_what=blocks,
                           summary=getattr(args, "summary", None),
                           evidence=getattr(args, "evidence", None))
        try:
            notify(rid, store=store)            # primary trigger: instant push
        except Exception as e:  # noqa: BLE001 — cron backstop will re-notify
            print(f"[approval] notify deferred to backstop: {e}", file=sys.stderr)
        print(rid)
        return 0
    if args.cmd == "patch":
        # Additive in-place update of a pending human_task row (commitment reshape /
        # backfill). SILENT: no approval_notify (gm guardrail — never re-notify an
        # already-delivered card). question mirrors block_task in the store.
        fields = {}
        for k in ("feature", "block_task", "blocks_what", "summary", "evidence"):
            v = getattr(args, k, None)
            if v is not None:
                fields[k] = v
        ok = store.update_fields(args.id, **fields)
        print(args.id if ok else "")
        return 0 if ok else 4
    if args.cmd == "get":
        row = store.get(args.id)
        if row is None and args.id.startswith("qnr_"):
            # gm-gen13's first-hour catch (msg_564caceb): this verb CREATES
            # questionnaires but could not READ them back — a verify path blind
            # to its own writes is the check-that-cannot-fail-independently
            # class in miniature. qnr ids live in the questionnaire store.
            row = _qstore().get(args.id)
        if row is None:
            print(json.dumps({"error": "not found", "id": args.id}))
            return 1
        print(json.dumps(row, indent=2, default=str))
        return 0
    if args.cmd == "ack":
        ok = store.ack(args.id)
        print(json.dumps({"acked": ok, "id": args.id}))
        return 0 if ok else 1
    if args.cmd == "questionnaire":
        if not _authorship_guard(args.from_agent):
            return 4
        # watch_gateway prints import-time banners to STDOUT (and drags the whole gateway +
        # Arturo stack in); keep stdout JSON-only for CLI consumers (same fix as the "answer"
        # and "pending" branches).
        _saved_stdout = sys.stdout
        try:
            sys.stdout = sys.stderr
            import watch_gateway as _wg
        finally:
            sys.stdout = _saved_stdout
        raw = args.questions_json
        if raw.startswith("@"):
            with open(os.path.expanduser(raw[1:])) as f:
                raw = f.read()
        try:
            questions = json.loads(raw)
            if not isinstance(questions, list) or not questions:
                raise ValueError("questions must be a non-empty list")
        except ValueError as e:
            print(f"[approval] bad --questions-json: {e}", file=sys.stderr)
            return 2
        if len(questions) > 20:
            print("[approval] max 20 questions (v1)", file=sys.stderr)
            return 2
        for i, q in enumerate(questions, 1):
            if not isinstance(q, dict) or not q.get("prompt"):
                print(f"[approval] question {i} needs a prompt", file=sys.stderr); return 2
            kind = q.get("kind", "free_text")
            if kind not in ("menu", "free_text"):
                print(f"[approval] question {i} kind must be menu|free_text", file=sys.stderr); return 2
            if kind == "menu":
                menu = q.get("menu") or {}
                opts = menu.get("options") or []
                if not opts or len(opts) > 10:
                    print(f"[approval] question {i} menu needs 1-10 options", file=sys.stderr); return 2
                for idx, o in enumerate(opts):
                    if "n" not in o:
                        o["n"] = str(idx + 1)
                _wg.stamp_input_kinds(menu)             # THE one classifier
                for o in opts:
                    if len(str(o.get("label", ""))) > 600:
                        print(f"[approval] question {i} label >600ch", file=sys.stderr); return 2
                    if (o.get("input_kind") or "direct") == "chat":
                        print(f"[approval] question {i}: chat options are excluded "
                              f"from questionnaires (F1)", file=sys.stderr); return 2
        qstore = _qstore(); qstore.migrate()
        qid = qstore.create(from_agent=args.from_agent, title=args.title, questions=questions,
                            summary=_stamp_delegation(args.summary, args.delegated_by),
                            thread_key=args.thread_key, op_key=args.op_key,
                            feature=args.feature, worker_kind=args.worker_kind, urgency=args.urgency)
        # Instant push (spec §5: title + "N questions", no per-question actions);
        # the card also arrives via the pending feed (pseudo-row) + SSE. PARK now.
        try:
            from approval_notify import notify_questionnaire
            notify_questionnaire(qid, store=qstore)
        except Exception as e:  # noqa: BLE001 — cron backstop will re-notify
            print(f"[approval] notify deferred to backstop: {e}", file=sys.stderr)
        print(qid)
        return 0
    if args.cmd == "qack":
        ok = _qstore().ack(args.id)
        print(json.dumps({"acked": ok, "id": args.id}))
        return 0 if ok else 1
    if args.cmd == "expire-sweep":
        # gm msg_86bcc168 item 4: past-expiry PENDING rows kept status 'pending' forever and
        # the author was never told (expire_due had no caller). Dry-run by default; --apply
        # writes + notifies. The `pending` feed is deliberately NOT filtered by expiry: the
        # API does not filter either (28/28 returned live), and the operator ruled nothing expires.
        dry = not args.apply
        rows = store.expire_due(dry_run=dry, notify_fn=None if dry else _expiry_notice)
        from approval_config import EXPIRE_PENDING as _ep
        print(json.dumps({"dry_run": dry, "gate_expire_pending": bool(_ep),
                          "due" if dry else "expired_rows": rows,
                          "expired": 0 if dry else len(rows),
                          "notified": 0 if dry else sum(1 for r in rows if r.get("notified"))},
                         default=str))
        return 0
    if args.cmd == "pending":
        # The canonical pending feed the web dashboard reads (SPEC R5 §2.3
        # dual-read: the web GET unions THIS with the frozen filesystem store).
        # Same rows, same options-decoding the gateway's /pending-approvals uses
        # — one feed, N transports. Read-only; never mutates.
        #
        # gm-mine-menu-card seam 1: additively carry the structured `menu` blob
        # (question/options[{n,label,input_kind}]) alongside the pre-existing
        # flat `options` (label strings) column, using the SAME parser
        # (watch_gateway._menu) the gateway's /pending-approvals uses, so a
        # kind='menu' row round-trips identically through both transports. No
        # existing field changes shape; `menu` is None for non-menu rows.
        # watch_gateway prints import-time banners to STDOUT; redirect stdout
        # -> stderr across the import so stdout stays JSON-only (same fix as
        # the "answer" branch below).
        _saved_stdout = sys.stdout
        try:
            sys.stdout = sys.stderr
            import watch_gateway as _wg
        finally:
            sys.stdout = _saved_stdout
        rows = store.pending_to_notify()
        out = [{"id": r["id"], "from_agent": r["from_agent"], "question": r["question"],
                "op_key": r.get("op_key"),
                "options": json.loads(r["options"]) if isinstance(r.get("options"), str) else r.get("options"),
                "status": r.get("status"), "created_at": r.get("created_at"),
                "kind": r.get("kind"), "summary": r.get("summary"),
                "menu": _wg._menu(r),
                "risk_level": r.get("risk_level"), "reversibility": r.get("reversibility"),
                "feature": r.get("feature"), "provider": r.get("provider"),
                # §6.1 human_task feed export (additive; None on legacy/unarmed rows).
                "block_task": r.get("block_task"), "blocks_what": r.get("blocks_what"),
                "snoozed_until": r.get("snoozed_until")} for r in rows]
        print(json.dumps(out, default=str))
        return 0
    if args.cmd == "answer":
        # Land a web answer through the ONE shared state machine (§5). The web
        # route provides no validation of its own — everything (menu/option/
        # free-text/qnr_/perm: rules + record_answer -> fire_resume) happens in
        # watch_gateway.apply_answer, the SAME core the gateway uses. fire=True:
        # the resume fires synchronously here (no event loop to defer to).
        # watch_gateway prints import-time banners to STDOUT (intent router,
        # etc.); redirect stdout->stderr across the import so stdout stays
        # JSON-only for the shell-caller (the Node route parses stdout).
        _saved_stdout = sys.stdout
        try:
            sys.stdout = sys.stderr
            import watch_gateway as _wg
        finally:
            sys.stdout = _saved_stdout
        # R7d: forward the provenance tags into the ONE core. The web bridge
        # passes `--surface web --answered-by shaw` (it holds the authenticated
        # web session, so asserting 'operator' is trusted). On the DEFAULT agent_cli
        # edge `--answered-by` is self-asserted — the same content-vs-authorship
        # plane as `from_agent` (spec §4.1): an agent may pass any principal, so
        # this value MUST NEVER be read as authenticated. It binds to a verified
        # seat identity only at R8; until then it is audit provenance, never authz.
        result = _wg.apply_answer(store, args.rid, args.answer, text=args.text,
                                  option_n=args.option_n, answer_text=args.answer_text,
                                  fire=True, surface=args.surface,
                                  answered_by=args.answered_by)
        result.pop("resume_row", None)   # not JSON-relevant to the caller
        print(json.dumps(result, default=str))
        return 0 if result.get("ok") else 1

if __name__ == "__main__":
    sys.exit(main())
