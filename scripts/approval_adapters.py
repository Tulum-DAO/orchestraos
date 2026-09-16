"""Provider-neutral approval CREATION adapters (spec §4, R4 — Phase 1).

A Claude agent and a Gemini/agy agent create an approval through the SAME
canonical SERVICE — the `approval.py request` CLI contract. the operator's
service-boundary ruling (2026-08-23): one service owns `ApprovalStore`; a
provider adapter is a THIN caller that NEVER imports the store module, never
opens the DB, and never runs its own SQL. Per-adapter DB mutation is exactly the
multi-writer corruption class that wiped tasks.db 08-23; concentrating all writes
behind the one service keeps schema evolution N-adapter-decoupled.

The adapter's ONLY two jobs (spec §4):
  (a) invoke the service from its runtime, and
  (b) capture the identity fields NATIVE to that runtime — never self-declared by
      the model:
        * seat_id / run_id / generation  ← the registry row (single source)
        * seat_epoch                      ← cv4_seat_epochs (passed in when it
                                            exists on-DB; nullable until then)
        * provider                        ← the adapter's own runtime (provenance)
        * provider_session_id             ← Claude sid / agy brain id
        * process_instance_id             ← this OS process incarnation (PID +
                                            start-time; provider-NEUTRAL)

Nothing downstream branches on `provider`; the ONLY provider-specific thing here
is WHICH identity source each thin entrypoint reads. C8 verdict (2026-08-23):
Antigravity's native `ask_question` tool exists and blocks, but its answer loop
lives in agy's own PendingApprovals queue and the PreToolUse hook contract
(allow/deny/ask/force_ask + arg-overwrite) has NO channel to inject the operator's
canonical answer back as a tool result — so the hook path cannot route through
the canonical `fire_resume` and is strictly DOMINATED by this CLI-service
adapter, which is therefore the Phase-1 Gemini adapter for BOTH providers.

HERMETIC/CANARY-ONLY (the operator ruling, spec §7 R4 row): no PRODUCTION Gemini seat
uses these adapters until R8 identity fencing arms. Provider emission develops
hermetically first.
"""
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APPROVAL_CLI = os.path.join(_HERE, "approval.py")


def _proc_start_ticks() -> str:
    """Best-effort process start-time (Linux /proc/self/stat field 22), so
    process_instance_id distinguishes two incarnations that reuse a PID. Neutral
    across runtimes; falls back to '' where /proc is unavailable."""
    try:
        with open("/proc/self/stat") as f:
            return f.read().split(") ", 1)[1].split()[19]
    except Exception:
        return ""


def _process_instance_id() -> str:
    start = _proc_start_ticks()
    return f"{os.getpid()}:{start}" if start else str(os.getpid())


def _registry_identity(agent_id, registry_path=None):
    """seat_id / run_id / generation FROM the registry row (never self-declared).
    seat_id is the stable lineage address (== agent_id). A missing row yields
    seat_id only — the service still stamps a coherent, non-guessed identity."""
    reg_path = registry_path or os.path.join(os.path.dirname(_HERE), "registry.json")
    entry = {}
    try:
        with open(reg_path) as f:
            entry = (json.load(f).get("agents", {}) or {}).get(agent_id) or {}
    except (OSError, json.JSONDecodeError):
        entry = {}
    return {
        "seat_id": agent_id,
        "run_id": entry.get("run_id"),
        "generation": entry.get("generation"),
    }


def create_approval(agent_id, question, *, provider, provider_session_id,
                    worker_kind="pane", registry_path=None, db_path=None,
                    seat_epoch=None, options=None, summary=None, feature=None,
                    thread_key=None, op_key=None, process_lease_id=None,
                    origin=None, generation=None, run_id=None,
                    menu=None, menu_json=None):
    """Provider-NEUTRAL core: capture identity, invoke the canonical service,
    return the `apr_*` id. This function contains ZERO provider branches — the
    per-provider entrypoints below differ only in the identity SOURCE they read
    before delegating here.

    Raises RuntimeError if the service refuses (non-zero exit)."""
    ident = _registry_identity(agent_id, registry_path)
    argv = [sys.executable, _APPROVAL_CLI, "request", question,
            "--from", agent_id, "--worker-kind", worker_kind,
            "--seat-id", ident["seat_id"],
            "--provider", provider,
            "--provider-session-id", str(provider_session_id or ""),
            "--process-instance-id", _process_instance_id()]
    effective_run_id = run_id if run_id is not None else ident["run_id"]
    if effective_run_id is not None:
        argv += ["--run-id", str(effective_run_id)]
    effective_gen = generation if generation is not None else ident["generation"]
    if effective_gen is not None:
        argv += ["--generation", str(effective_gen)]
    if seat_epoch is not None:
        argv += ["--seat-epoch", str(seat_epoch)]
    if origin is not None:
        argv += ["--origin", origin]
    effective_lease = process_lease_id
    if effective_lease is None:
        try:
            from continuity import authority
            effective_lease = authority.get_or_acquire_lease(
                seat=ident["seat_id"],
                holder_id=f"{ident['seat_id']}-{_process_instance_id()}",
                ttl_s=86400,
                authorized_by=f"{provider}-adapter",
                db=db_path or authority.DEFAULT_DB
            )
        except Exception:
            effective_lease = None
    if effective_lease is not None:
        argv += ["--process-lease-id", str(effective_lease)]
    if menu_json is not None:
        argv += ["--menu-json", menu_json if isinstance(menu_json, str) else json.dumps(menu_json)]
    elif menu is not None:
        argv += ["--menu-json", json.dumps(menu) if isinstance(menu, dict) else str(menu)]
    if options:
        argv += ["--options", ",".join(options)]
    if summary is not None:
        argv += ["--summary", summary]
    if feature is not None:
        argv += ["--feature", feature]
    if thread_key is not None:
        argv += ["--thread-key", thread_key]
    if op_key is not None:
        argv += ["--op-key", op_key]

    env = dict(os.environ)
    if db_path:
        env["APPROVAL_DB_PATH"] = db_path
    proc = subprocess.run(argv, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"approval service refused (exit {proc.returncode}): {proc.stderr.strip()}")
    out = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not out or not out[-1].startswith("apr_"):
        raise RuntimeError(f"approval service returned no apr_ id: {proc.stdout!r}")
    return out[-1]


# --- The adapter EDGE: the only provider-specific code (identity source) -------

def claude_create_approval(agent_id, question, *, provider_session_id=None, **kw):
    """Claude adapter: provider_session_id is the Claude session id (sid). Falls
    back to the CLAUDE_SESSION_ID env when not passed explicitly."""
    sid = provider_session_id or os.environ.get("CLAUDE_SESSION_ID")
    return create_approval(agent_id, question, provider="claude",
                           provider_session_id=sid, **kw)


def gemini_create_approval(agent_id, question, *, provider_session_id=None, **kw):
    """Gemini/agy adapter: provider_session_id is the agy brain/conversation id
    (~/.gemini/antigravity-cli/brain/<id>). Falls back to the AGY_BRAIN_ID env.
    Uses the SAME service + row shape as the claude adapter (C8 CLI-adapter
    verdict) — the only difference is which native id is captured here."""
    brain = provider_session_id or os.environ.get("AGY_BRAIN_ID")
    return create_approval(agent_id, question, provider="gemini",
                           provider_session_id=brain, **kw)


def _codex_session_from_fd(pid: int | None = None) -> str | None:
    """Codex identity source #1 (dossier §5 preferred order): the rollout JSONL
    the codex process holds OPEN is a positive pid->session binding — never
    model prose, never the most-recent session file. Returns the session uuid
    from the rollout filename, or None (caller falls back to env / refuses)."""
    import re as _re
    pid = pid or os.getppid()
    seen = set()
    # walk up a few ancestors: adapter may run as a grandchild of the codex proc
    for _ in range(4):
        if pid in seen or pid <= 1:
            break
        seen.add(pid)
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    tgt = os.readlink(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                m = _re.search(
                    r"/\.codex/sessions/.+/rollout-[\dT-]+-([0-9a-f-]{36})\.jsonl$",
                    tgt)
                if m:
                    return m.group(1)
            with open(f"/proc/{pid}/stat") as f:
                data = f.read()
                rparen = data.rfind(")")
                if rparen != -1:
                    pid = int(data[rparen + 1:].split()[1])   # ppid is 2nd token after comm
                else:
                    break
        except (OSError, IndexError, ValueError):
            break
    return None


def codex_create_approval(agent_id, question, *, provider_session_id=None, **kw):
    """Codex adapter (C-R4, SPEC_codex-parity-phase2): provider_session_id is
    the codex rollout session uuid. Identity source order (dossier §5): the
    OPEN rollout FD of the calling process tree, then the CODEX_SESSION_ID env.
    Ambiguity => provider_session_id stays empty ('' via core) — the row is
    still created (legacy-shaped) and the R8 fence simply has nothing to
    enforce, which is the fail-safe direction. Same canonical service, same row
    shape as claude/gemini (approval.py already accepts --provider codex)."""
    sid = (provider_session_id
           or _codex_session_from_fd()
           or os.environ.get("CODEX_SESSION_ID"))
    return create_approval(agent_id, question, provider="codex",
                           provider_session_id=sid, **kw)
