# Track 11 — Autonomous blue-green rotation on every runtime

Size: L · Labels: `track`, `core`, `rotation` · default ON

## Problem

The rotation driver (`scripts/lineage_daemon/cron_beat.py`) is real and runs one
fleet beat per cron tick — arm, prewarm, readiness, quota gate, swap, verify — but
today it's proven on Claude Code only. Two separate adapter layers exist and it is
easy to conflate them (verified by effect, rotation-autonomy-builder review):
`scripts/lineage_daemon/wal/ctx_adapters.py::read_ctx` is the **real, live**
context read the beat actually calls (`wal/bg_beat.py:39`) — it dispatches per
runtime: the Claude detector's `/tmp/claude-ctx-<sid>.json`, codex rollout token
counts (reliable), and a Gemini token/1M-window approximation. Separately,
`scripts/lineage_daemon/adapters/{claude,gemini,codex}_adapter.py` implement a
`ProviderAdapter` protocol (`observe_context`, `spawn_alias`,
`resolve_declared_identity`, `locate_transcript`, `resume_command`) whose methods
are still stubbed (`observe_context` always returns
`context_pct=0.5, is_near_limit=False`; `spawn_alias` always returns
`session_id="mock_sid"`) — but only `adapters/protocol.py`'s dataclasses
(`RetirementReceipt`/`RepinReceipt`) are imported on the live path (`executors.py`);
the stub `observe`/`spawn` methods are not on the beat's decision path. So the
Claude context read is already real, not mocked — the stub layer is a separate,
not-yet-wired consolidation effort. The `orchestra rotate` CLI command referenced
by the hackathon plan is not on `main` yet (only in the unmerged `tier0/spawn-rotate`
branch — `orchestra rotate --help` fails on main today; the main-today driver is
`python3 scripts/rotate_agent.py <seat>`, which runs the same arm→prewarm→
readiness→swap→verify sequence). Known portability gaps: `wal/green_liveness.py`
is Claude-specific (pane events + transcript assistant-turn detection); Gemini's
`ctx_adapters` reader can return an out-of-range token ratio, forcing a stale
fallback — a concrete, live gap, not a hypothetical; `wal/green_quota.py` is
Codex-specific (reads the rollout's `rate_limits`/`has_credits` — real and
structured — Gemini has no reader registered at all); a tmux-on-one-host
assumption throughout the beat; and capture-based readiness probes (reading a
pane's rendered text, which differs per CLI's TUI).

For the fuller detail behind each runtime gap, see `docs/ROTATION.md` (landed on
`main`) — treat it as the source of truth over this section if the two disagree.

## Design

Make one full, lossless rotation run on a clean install for each runtime, in this
order: claude (the reference implementation — the beat and its gates were built
against it, so this is "prove it," not "build it"), then gemini, then codex,
documenting or fixing each portability gap as it's hit rather than guessing them
all up front. **Claude's context read is already real** (via `ctx_adapters.py`) —
do not spend track time replacing `adapters/claude_adapter.py`'s stub
`observe_context`; it is dead weight on the Claude leg, not a blocker. Gemini and
Codex's real gaps are narrower and already known: Gemini needs an honest context
read (`ctx_adapters.py`'s gemini reader, not the adapter stub) or an explicit
"context unsupported" finding if one genuinely can't be built; Codex needs its
credit/quota gate (`green_quota.py`) proven against a real depleted-credits case,
not a context read (its rollout tokens are already reliable).

The Claude-specific liveness gate and Codex-specific quota gate are gates the beat
calls generically (`wal/green_liveness.py`, `wal/green_quota.py`) but whose checks
are hardcoded to one runtime's signals, and Gemini has no quota reader registered
at all. Generalize the call site to dispatch by runtime rather than special-casing
inside the beat; a runtime with no equivalent gate (e.g. no quota concept) should
get an explicit no-op, not a silently-skipped check.

**Ownership:** rotation-autonomy-builder's standing commission from gm already IS
the Claude leg of this track (one real rotation on a clean install, gated on the
quota-oracle fix clearing `green_quota`'s ModuleNotFound — their
`docs/ROTATION.md` work has already merged). Track 11's Claude acceptance is that
deliverable — coordinate rather than duplicate it; a contributor picking up this
track should start on Gemini or Codex, or pair with rotation-autonomy-builder on
Claude, not redo it solo.

## Files you will touch

- `scripts/lineage_daemon/wal/ctx_adapters.py` — the real per-runtime context
  read (`read_ctx`); this is where Gemini's honest-context gap actually gets
  fixed, not in `adapters/gemini_adapter.py`.
- `scripts/lineage_daemon/wal/green_liveness.py`, `green_quota.py` — generalize
  the runtime-specific checks behind a per-runtime dispatch; register a Gemini
  quota reader (currently absent) or an explicit no-op.
- `scripts/lineage_daemon/adapters/*.py` — out of scope for the Claude leg (its
  stub is not on the decision path); only touch `gemini_adapter.py` /
  `codex_adapter.py` if this track's Gemini/Codex work chooses to wire the
  protocol layer up as part of closing those runtimes' gaps, not as a
  prerequisite.
- `scripts/lineage_daemon/cron_beat.py` — confirm the beat calls through the
  gate abstraction generically; today's soft-only posture (see the file's own
  docstring, `SKIP_SOFT_ONLY`) should stay intact per-runtime unless an operator
  ruling says otherwise.
- `scripts/rotate_agent.py` — the main-today manual-rotation driver (arm→prewarm→
  readiness→swap→verify); `orchestra_cli/__main__.py`'s `orchestra rotate --auto`
  is the target CLI once `tier0/spawn-rotate` merges — confirm current merge
  state before relying on it.
- Fixture transcripts for each runtime (new, likely under
  `scripts/lineage_daemon/fixtures/` or the adapter tests' existing pattern) — a
  clean-install rotation needs something to swap toward before a real live seat
  exists to test against.

## Steps

1. `orchestra rotate --help` (expect it to fail on main today) and
   `grep -n "read_ctx\|context_pct=0.5\|mock_sid" scripts/lineage_daemon/wal/bg_beat.py scripts/lineage_daemon/adapters/*.py`
   — confirm today's actual state (which layer the beat really calls, command
   availability) before claiming anything is broken or working.
2. Claude first: this leg is rotation-autonomy-builder's standing commission — pair
   with them or pick up Gemini/Codex instead of duplicating it. If picking up
   Claude anyway: on a clean install, spawn one seat, drive it near a context
   ceiling (or use a fixture transcript), run `python3 scripts/rotate_agent.py
   <seat>` (or `orchestra rotate --auto <seat>` once merged) for a full
   green→promote→verify cycle. Confirm nothing is lost: the successor answers the
   predecessor's canary, the registry's canonical pointer moves, the old generation
   is retired cleanly.
3. Gemini: fix the honest-context read in `ctx_adapters.py` (today's reader can
   return an out-of-range token ratio, forcing a stale fallback — reproduce that
   first, then fix it or document why it can't be), register a quota reader or an
   explicit no-op in `green_quota.py`, then run the same full cycle.
4. Codex: its context read (rollout tokens) is already reliable — the real gap is
   `green_quota.py`'s credit gate. Include a documented dry test of the
   depleted-credits failure path (a real vendor-specific stall with no generic
   error today), not just the happy path.
5. For any runtime where you cannot complete a real live rotation in the time
   available, produce the fixture-transcript version instead and say so plainly in
   the PR — "documented, not done" is an acceptable outcome per this track's
   acceptance test; a claimed pass that wasn't run for real is not.

## Acceptance test

Claude: a real rotation (not a fixture) completes green → promote → verify on a
clean install, successor's readback checked against the predecessor's actual
state — via `scripts/rotate_agent.py` today, or `orchestra rotate --auto <seat>`
once that command merges. Gemini and Codex are either done the same way or
explicitly documented with what's missing and why — per-runtime status must be
stated, not implied by silence.

## Start prompt

```
I'm working Track 11 (autonomous rotation on every runtime) for the
OrchestraOS hackathon. The Claude leg is already rotation-autonomy-builder's
standing commission (one real clean-install rotation, gated on a quota-oracle
fix clearing green_quota's ModuleNotFound) -- coordinate with them rather
than duplicating it; pick up Gemini or Codex, or pair with them on Claude.
Read docs/tracks/11-autonomous-rotation-all-runtimes.md for the full design,
and docs/ROTATION.md (landed on main) for the authoritative per-runtime gap
detail.
Files to touch: scripts/lineage_daemon/wal/ctx_adapters.py (the REAL context
read the beat calls -- not adapters/*.py, which is an unwired stub layer),
scripts/lineage_daemon/wal/green_liveness.py and green_quota.py (generalize
behind per-runtime dispatch; Gemini has no quota reader registered).
Start by running Step 1's grep to see which layer the beat actually calls
before touching anything -- the mock adapters are a red herring for the
Claude leg.
```

## Out of scope

- Moving off the tmux-on-one-host assumption (a real portability gap, but a
  separate, larger architectural change than this track's per-runtime adapter
  work).
- Flipping the beat from soft-only to hard rotation by default — that is a
  separate operator decision (see `cron_beat.py`'s own docstring), not something
  this track changes.
- New runtimes beyond claude/gemini/codex.
