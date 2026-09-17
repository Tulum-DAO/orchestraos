# Track 11 — Autonomous blue-green rotation on every runtime

Size: L · Labels: `track`, `core`, `rotation` · default ON

## Problem

The rotation driver (`scripts/lineage_daemon/cron_beat.py`) is real and runs one
fleet beat per cron tick — arm, prewarm, readiness, quota gate, swap, verify — but
today it's proven on Claude Code only, and the provider-adapter layer meant to make
it runtime-agnostic is a stub. `scripts/lineage_daemon/adapters/claude_adapter.py`
implements the `ProviderAdapter` protocol (`observe_context`, `spawn_alias`,
`resolve_declared_identity`, `locate_transcript`, `resume_command`) but every method
returns hardcoded mock data — `observe_context` always returns
`context_pct=0.5, is_near_limit=False`; `spawn_alias` always returns
`session_id="mock_sid"`. `gemini_adapter.py` and `codex_adapter.py` exist alongside
it with the same shape. The `orchestra rotate` CLI command referenced by the
hackathon plan is not on `main` yet (only in the unmerged `tier0/spawn-rotate`
branch as of this writing — verify with `orchestra rotate --help` before trusting
this sentence). Known portability gaps beyond the adapter stubs: a Claude-specific
liveness gate (`scripts/lineage_daemon/wal/green_liveness.py`), a Codex-specific
credit/quota gate (`scripts/lineage_daemon/wal/green_quota.py`), a tmux-on-one-host
assumption throughout the beat, and capture-based readiness probes (reading a
pane's rendered text, which differs per CLI's TUI).

## Design

Make one full, lossless rotation run on a clean install for each runtime, in this
order: claude (the reference implementation — the beat and its gates were built
against it, so this is "prove it," not "build it"), then gemini, then codex,
documenting or fixing each portability gap as it's hit rather than guessing them
all up front.

For each runtime: replace the mock `ProviderAdapter` implementation with a real
one — `observe_context` must read that runtime's actual context/token signal
(Claude: parse the transcript JSON for token usage; Gemini/Codex: find their
equivalent signal, which may not exist in the
same shape — if a runtime genuinely cannot report context usage, that's a finding
to document, not a mock to leave in place), `spawn_alias`/`resolve_declared_identity`
must launch and identify a real successor session, `locate_transcript` must point
at that runtime's real transcript file, `resume_command` must be a real,
executable resume string.

The Claude-specific liveness gate and Codex-specific quota gate are gates the beat
calls generically (`wal/green_liveness.py`, `wal/green_quota.py`) but whose checks
are hardcoded to one runtime's signals. Generalize the call site to dispatch by
runtime (same pattern as the adapter protocol) rather than special-casing inside
the beat; a runtime with no equivalent gate (e.g. no quota concept) should get an
explicit no-op adapter, not a silently-skipped check.

## Files you will touch

- `scripts/lineage_daemon/adapters/claude_adapter.py`,
  `gemini_adapter.py`, `codex_adapter.py`, `protocol.py` — replace mocked methods
  with real implementations; `protocol.py` only if the interface itself needs a new
  method to express a runtime-specific gap honestly (e.g. "context unsupported"
  rather than a fabricated percentage).
- `scripts/lineage_daemon/wal/green_liveness.py`, `green_quota.py` — generalize
  the runtime-specific checks behind the adapter dispatch.
- `scripts/lineage_daemon/cron_beat.py` — confirm the beat calls through the
  adapter/gate abstraction generically; today's soft-only posture (see the file's
  own docstring, `SKIP_SOFT_ONLY`) should stay intact per-runtime unless an
  operator ruling says otherwise.
- `orchestra_cli/__main__.py` — `orchestra rotate --auto <seat>` (confirm current
  merge state; this is the command the acceptance test below invokes).
- Fixture transcripts for each runtime (new, likely under
  `scripts/lineage_daemon/fixtures/` or the adapter tests' existing pattern) — a
  clean-install rotation needs something to swap toward before a real live seat
  exists to test against.

## Steps

1. `orchestra rotate --help` and `grep -n "context_pct=0.5\|mock_sid" scripts/lineage_daemon/adapters/*.py`
   — confirm today's actual state (command availability, mock scope) before
   claiming anything is broken or working.
2. Claude first: on a clean install, spawn one seat, drive it near a context
   ceiling (or use a fixture transcript if the beat supports a dry-run/fixture
   mode), let the beat (or `orchestra rotate --auto <seat>`) run a full
   green→promote→verify cycle. Confirm nothing is lost: the successor answers the
   predecessor's canary, the registry's canonical pointer moves, the old generation
   is retired cleanly.
3. Repeat for gemini: implement `gemini_adapter.py`'s real methods first (its
   context signal, its spawn/resume shape), then run the same full cycle. Where a
   gate has no runtime-agnostic equivalent (e.g. no quota concept for this CLI),
   document that explicitly rather than faking a pass.
4. Repeat for codex, including its credit/quota gate — this is the one with a real
   vendor-specific failure mode (depleted premium credits stall a swap with no
   generic error), so the codex run should include a documented dry test of that
   failure path, not just the happy path.
5. For any runtime where you cannot complete a real live rotation in the time
   available, produce the fixture-transcript version instead and say so plainly in
   the PR — "documented, not done" is an acceptable outcome per this track's
   acceptance test; a claimed pass that wasn't run for real is not.

## Acceptance test

`orchestra rotate --auto <seat>` completes green → promote → verify on a clean
install for Claude, proven by a real run (not a fixture) with the successor's
readback checked against the predecessor's actual state. Gemini and Codex are
either done the same way or explicitly documented with what's missing and why —
per-runtime status must be stated, not implied by silence.

## Start prompt

```
I'm working Track 11 (autonomous rotation on every runtime) for the
OrchestraOS hackathon, owned by seat rotation-autonomy-builder.
Read docs/tracks/11-autonomous-rotation-all-runtimes.md in this repo for
the full design. Files to touch: scripts/lineage_daemon/adapters/*.py
(replace the mocked ProviderAdapter methods with real per-runtime
implementations), scripts/lineage_daemon/wal/green_liveness.py and
green_quota.py (generalize behind adapter dispatch).
Start by running `orchestra rotate --help` and grepping the adapters for
the mock returns (Step 1) to get an honest baseline of what's real vs
stubbed before touching anything, then do Claude end to end for real
before starting Gemini or Codex — Claude is the reference implementation
the beat's gates were already built against.
```

## Out of scope

- Moving off the tmux-on-one-host assumption (a real portability gap, but a
  separate, larger architectural change than this track's per-runtime adapter
  work).
- Flipping the beat from soft-only to hard rotation by default — that is a
  separate operator decision (see `cron_beat.py`'s own docstring), not something
  this track changes.
- New runtimes beyond claude/gemini/codex.
