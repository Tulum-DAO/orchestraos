"""Per-provider discriminator profiles — the ONE source of the measured 2x2
constants for the B1(a) status deriver.

Provider-agnostic-by-abstraction (the runtime_signatures precedent, the operator-approved
apr_a72c10f8): the pty byte tap + /proc pid-tree sampling + the 2x2 shape are
kernel seams (provider-blind), but the CONSTANTS (idle CPU baseline, epsilons,
chrome signatures) differ per runtime and MUST NOT be tuned on claude and shipped
to codex/gemini (the A0 "false stalls on codex/gemini" failure).

Sources:
  * claude + gemini constants are pinned to the A0 golden fixtures
    (contract/transcript/fixtures/<p>/a0-status-discriminator.json), which A0
    distilled from real byte streams + /proc samples.
  * the CODEX idle baseline was UNMEASURED by A0 (a named TODO). B1 MEASURED it
    live on codex-dev-1 (idle "> Ask Codex to do anything" placeholder, pid-tree
    of 3): sustained 3.3-5.2% core over 15-20s windows, bursty 2.4-9.5% over 2s
    windows — a live TUI render loop. THE finding: codex idle CPU (2.4-9.5%)
    ENGULFS codex-compute (>=1.46% per A0), so the CPU axis cannot separate
    idle from compute for codex; bytes + IO-delta are primary, CPU is a weak
    hint only. That is why `cpu_axis_reliable=False` for codex.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    idle_baseline_core_pct: float          # measured per-provider idle CPU floor
    idle_band_core_pct: tuple              # (lo, hi) window-jitter band
    eps_cpu_core_pct: float                # COMPUTING threshold ABOVE baseline
    eps_io_bytes: int                      # IO-delta threshold (codex-primary + tiebreak)
    window_s: float
    smoothing: str                         # 'none' | 'median-of-3'
    cpu_axis_reliable: bool                # False for codex (render loop confounds idle/compute)
    idle_baseline_measured: bool           # closes the A0 codex TODO when True
    prompt_char: str
    store_side_semantics: bool = False     # gemini only: permission signal in the durable store
    hook_recency_s: float = 0.0            # E1: a lifecycle hook this fresh (s) vetoes a false
                                           # stall (proof-of-life). 0 = disabled (back-compat)
    chrome: dict = field(default_factory=dict)


# claude — near-zero idle; the star glyph is DECORATIVE (never a working marker,
# A0: 5/5 idle panes false-fired on it). Working marker = 'esc to interrupt' or
# byte-flow. Hooks are the semantic layer (present only for claude).
_CLAUDE = ProviderProfile(
    provider="claude",
    # 2.1.260 REGRESSION (the operator-caught, gm-confirmed): claude idle CPU is no longer
    # ~0 — the 2.1.260 TUI runs an idle render loop with a WIDE, variable floor
    # (measured 0.0-4.6% core across idle seats). With baseline 0 + eps 0.5 that
    # false-fired "computing" on every idle seat. This is EXACTLY the codex finding
    # (idle CPU engulfs compute) now applying to claude, so — matching the codex
    # precedent for a variable floor — the CPU axis is UNRELIABLE for claude on
    # 2.1.260: bytes + IO-delta are primary, and claude also has the working HOOK
    # for compute intent. idle_baseline raised to the measured representative for
    # documentation (unused while cpu_axis_reliable=False).
    idle_baseline_core_pct=4.0,
    idle_band_core_pct=(0.0, 4.6),
    eps_cpu_core_pct=0.5,
    eps_io_bytes=4096,
    window_s=2.0,
    smoothing="none",
    cpu_axis_reliable=False,
    idle_baseline_measured=True,
    prompt_char="\u276f",
    # E1: claude reasoning agents (gm, orchestra-builder) flap streaming<->stalled on
    # normal >5s think/model-API gaps; a hook event within 10s is proof-of-life.
    # INERT until telemetryd emits hooks.age_s (that activation is congruence-gated).
    hook_recency_s=10.0,
    chrome={"working_re": "esc to interrupt",
            "permission_re": r"do you want to (proceed|allow|run|make|create|fetch)",
            "done_summary_re": r"\u273b .+ for \d+m",
            "star_glyph_is_decorative": True},
)

# gemini/agy — idle is a render loop (0.97-1.95% core), NOT ~0. Subtract the
# baseline before thresholding; single-window CPU jitters within the band so
# smoothing is median-of-3. Permission ALSO lives in the durable store (D1:
# steps.status {3,6,7} + permissions blob on step_type=132) — a hooks-absent
# semantic source independent of pty chrome.
_GEMINI = ProviderProfile(
    provider="gemini",
    idle_baseline_core_pct=1.4,
    idle_band_core_pct=(0.97, 1.95),
    eps_cpu_core_pct=0.6,
    eps_io_bytes=4096,
    window_s=2.0,
    smoothing="median-of-3",
    cpu_axis_reliable=True,
    idle_baseline_measured=True,
    prompt_char=">",
    store_side_semantics=True,
    chrome={"idle_footer_re": r"\? for shortcuts|Gemini [\d.]+ (Flash|Pro)",
            "working_re": r"esc to cancel|Generating",
            "permission_re": r"Apply this change\?|Allow execution\?|Waiting for user"},
)

# codex — B1-MEASURED idle baseline (the A0 TODO). Idle render loop 3.3-5.2%
# sustained, bursty to ~9.5%. Because this band engulfs codex-compute (>=1.46%),
# the CPU axis is UNRELIABLE for idle-vs-compute: bytes-axis primary, IO-delta
# decisive. The idle placeholder 'Ask Codex to do anything' PERSISTS during
# compute (A0) so it is never an idle signal either.
_CODEX = ProviderProfile(
    provider="codex",
    idle_baseline_core_pct=4.0,            # representative sustained idle (measured)
    idle_band_core_pct=(2.4, 9.5),         # measured window-dependent band
    eps_cpu_core_pct=0.6,                  # only used as a weak hint (cpu_axis_reliable=False)
    eps_io_bytes=4096,
    window_s=2.0,
    smoothing="median-of-3",
    cpu_axis_reliable=False,               # THE codex finding: CPU cannot separate idle/compute
    idle_baseline_measured=True,           # closes the named A0 TODO
    prompt_char="\u203a",
    chrome={"idle_placeholder_re": "Ask Codex to do anything",
            "working_re": r"\u25e6 Working \(\d+s \u2022 esc to interrupt\)",
            "permission_re": r"Allow command\?|Do you want to run",
            "idle_placeholder_persists_during_compute": True},
)

PROFILES = {"claude": _CLAUDE, "gemini": _GEMINI, "codex": _CODEX}
DEFAULT = "claude"


def profile_for(runtime):
    """Resolve a provider profile; unknown/None fails SAFE to claude (the
    runtime_signatures unknown->claude precedent — never widen a surface for an
    unrecognized runtime)."""
    rt = (runtime or "").strip().lower()
    if rt == "agy":
        rt = "gemini"
    return PROFILES.get(rt, PROFILES[DEFAULT])
