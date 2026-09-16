"""B1(a) status deriver — the 2x2 (pty-bytes x /proc pid-tree CPU) discriminator.

This DELETES the 300/900/600 TTL crutches (agent-status.py:48-50) and SUBSUMES
the provider-idle-detection commission: pty-byte-flow + /proc IS the kernel truth
that commission was chartered to find. Two status oracles = the disease just
cured in identity, so B1 kills (not feeds) the old TTL trap.

The classifier is PURE: it takes ONE already-sampled window (bytes flowing?,
cpu_core percent, io_delta bytes, live_pids, chrome flags, optional store/hooks)
and returns a status. The sampling (one /proc scan fanned to N agents, median
smoothing) lives in proc_sampler.py; the pty chrome flags come from the ANSI
strip + per-runtime signature match. This split keeps the safety-critical
decision logic trivially testable against the A0 golden fixtures.

Axis precedence (load-bearing):
  1. offline: pane gone / no live pids.
  2. STREAMING: bytes flowing -> alive, regardless of CPU (bytes-axis PRIMARY;
     a streaming pane can read ~0 CPU because output is IO-bound).
  3. waiting_permission: the SEMANTIC layer — hooks (claude) OR durable store
     (gemini) OR pty-signature-match chrome (codex/gemini, hooks-absent). Never
     assumes hooks are present.
  4. COMPUTING: work with no visible bytes. IO-delta above eps => computing
     (the codex-primary axis: codex-compute carries ~61KB IO while its CPU sits
     BELOW its own idle baseline). CPU above (per-provider baseline + eps) also
     => computing, but ONLY where the CPU axis is reliable (NOT codex).
  5. STALLED vs IDLE: a working-intent signal (working chrome / working hook)
     with zero bytes/compute => a true stall (hung tool); otherwise IDLE. The
     decorative star / idle footer is NEVER a working signal.
"""


def _waiting_permission(sample, profile, hooks):
    chrome = sample.get("chrome") or {}
    if chrome.get("permission"):                       # pty-signature match (any provider)
        return True
    store = sample.get("store") or {}
    if profile.store_side_semantics and store.get("permissions_blob_populated"):
        return True                                    # gemini durable-store recovery (no hooks)
    if hooks and hooks.get("state") == "waiting_permission":
        return True                                    # claude semantic hook
    return False


def _working_intent(sample, hooks):
    chrome = sample.get("chrome") or {}
    if chrome.get("working"):
        return True
    if hooks and hooks.get("state") == "working":
        # E2: a working HOOK is stale-by-emission when the transcript shows the turn
        # already COMPLETED — Claude emits no Stop on interrupt/kill, so the hook
        # file froze at working. turn_complete True => veto the working intent (idle,
        # not stalled). False (a real mid-tool hang, pending tool_use) or None (no
        # transcript signal / non-claude) => trust the hook exactly as before.
        if sample.get("turn_complete") is True:
            return False
        return True
    return False


def _hook_recent(sample, hooks, profile):
    """E1 (status-oscillation fix): a lifecycle hook event seen within
    `profile.hook_recency_s` is DEFINITIVE proof-of-life — the agent is working
    BETWEEN tool calls, where a network-bound model wait legitimately reads zero
    local bytes/cpu/io. That is the false-stall the deriver otherwise flaps on
    (gm / orchestra-builder). Requires a real age signal: absent it (age None, or
    recency disabled with hook_recency_s<=0) this is False and the classic stalled
    path is preserved verbatim (back-compat; inert until telemetryd emits age)."""
    recency = getattr(profile, "hook_recency_s", 0.0) or 0.0
    if recency <= 0:
        return False
    age = hooks.get("age_s") if hooks else None
    if age is None:
        age = sample.get("hook_event_age_s")
    return age is not None and age >= 0 and age < recency


def _is_computing(sample, profile):
    io = sample.get("io_delta", 0) or 0
    if io > profile.eps_io_bytes:                      # IO-delta: codex-primary + universal tiebreak
        return True
    if profile.cpu_axis_reliable:
        cpu_above = (sample.get("cpu_core", 0.0) or 0.0) - profile.idle_baseline_core_pct
        if cpu_above > profile.eps_cpu_core_pct:
            return True
    return False


def derive_status(sample, *, profile, hooks=None):
    """Classify ONE sampled window into a fleet status string. Pure; no IO.

    `sample` keys (all optional, absent => falsy): bytes_flowing, chars_per_s,
    cpu_core (percent of ONE core, per-provider-baseline-relative handled here),
    io_delta (bytes since last window), live_pids, pane_gone, chrome{working,
    permission,idle_footer,star_glyph,idle_placeholder}, store{step_type,
    permissions_blob_populated}. `hooks` is the CLAUDE-ONLY semantic layer and
    may be None for every other runtime (graceful hooks-absent degrade)."""
    # 1. offline
    if sample.get("pane_gone") or sample.get("live_pids", 1) == 0:
        return "offline_crashed"
    # 2. bytes-axis PRIMARY
    if sample.get("bytes_flowing"):
        return "streaming"
    # 3. semantic waiting-for-permission (hooks OR store OR pty signature)
    if _waiting_permission(sample, profile, hooks):
        return "waiting_permission"
    # 4. computing (IO primary + reliable-CPU secondary)
    if _is_computing(sample, profile):
        return "computing"
    # 5. stalled (working intent, zero activity) vs idle
    if _working_intent(sample, hooks):
        # E1: a RECENT lifecycle hook event proves the agent is alive between tool
        # calls (bursty reasoning cadence) -> computing, not stalled. Only a STALE
        # working intent with zero activity is a true hang.
        if _hook_recent(sample, hooks, profile):
            return "computing"
        return "stalled"
    return "idle"
