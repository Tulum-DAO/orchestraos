/**
 * RED-first for DEC-1790826247484632 (orchestraos-claude + orchestraos-agy APPROVE over the
 * final spec, .workspace/proposals/parked-rotation-leftovers-spec.md).
 *
 * A rotation leaves a per-generation row behind. #137 taught the fleet view to hide two kinds
 * of ghost — `retired`/`archived`, and a `-genN` row whose base seat is alive. A third kind is
 * still counted: a leftover whose status is `parked`. Of 216 parked rows in the live registry,
 * ZERO have a live session, and 154 are rotation leftovers by the rule below.
 *
 * The negative cases are the point of this file. The first version of this rule hid `gm-g3`,
 * `ios-watch-dev-g19`, `orchestra-builder-g68` and `release-readiness-g3` — canonical lineage
 * roots in their own right, one of them carrying two generations of its own lineage. Condition
 * 5 exists because of that, and `own_id_is_a_canonical_root_is_never_flagged` is the test that
 * keeps it from coming back.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCanonicalIndex, supersededBy } from './rotation-leftovers.js';

/** The shape `getCanonicalAgents()` returns, trimmed to what the rule reads. */
const CANON = {
  'gm': { root: 'gm', tmux_session: 'gm' },
  'gm-g3': { root: 'gm-g3', tmux_session: 'gm-g3' },
  'ios-watch-dev': { root: 'ios-watch-dev', tmux_session: 'ios-watch-dev' },
  // A root whose own NAME carries a generation suffix. These are real: measured in the live
  // registry, `gm-g3` and `ios-watch-dev-g19` are canonical roots, not leftovers, and
  // `ios-watch-dev-g19` carries two generations of its own lineage.
  'ios-watch-dev-g19': { root: 'ios-watch-dev-g19', tmux_session: 'ios-watch-dev-g19' },
  'devex-review': { root: 'devex-review', tmux_session: 'devex-review' },
  // A root whose live head is a DIFFERENT session name — the green-alias case.
  'orchestraos-builder': { root: 'orchestraos-builder', tmux_session: 'orchestraos-builder-g4' },
} as any;

const index = buildCanonicalIndex(CANON)!;

const leftover = { id: 'devex-review-gen7', defStatus: 'parked', alive: false };

// ---- the rule fires ------------------------------------------------------------------------

test('a parked -genN row whose base is a canonical root is flagged', () => {
  assert.equal(supersededBy({ ...leftover, index }), 'devex-review');
});

test('the -gN shape is treated the same as -genN', () => {
  assert.equal(supersededBy({ id: 'gm-g9', defStatus: 'parked', alive: false, index }), 'gm');
});

// ---- condition 5: the v1 regression, locked down -------------------------------------------

test('own_id_is_a_canonical_root_is_never_flagged', () => {
  // gm-g3 satisfies conditions 1-4 exactly: -gN shape, parked, base `gm` IS a canonical root,
  // and not live. v1 hid it. It is a lineage root of its own and must stay visible.
  for (const id of ['gm-g3', 'ios-watch-dev-g19']) {
    assert.equal(supersededBy({ id, defStatus: 'parked', alive: false, index }), null, id);
  }
});

test('an id that is some root\'s canonical live-head session is never flagged', () => {
  // `orchestraos-builder-g4` is the live head OF root `orchestraos-builder`, not a leftover.
  assert.equal(
    supersededBy({ id: 'orchestraos-builder-g4', defStatus: 'parked', alive: false, index }),
    null,
  );
});

// ---- the other conditions ------------------------------------------------------------------

test('a base that is not a canonical root is not evidence of a rotation', () => {
  // lessons-historian-g4: base `lessons-historian` is no lineage at all.
  assert.equal(
    supersededBy({ id: 'lessons-historian-g4', defStatus: 'parked', alive: false, index }),
    null,
  );
});

test('a row that is alive is never flagged, whatever its status says', () => {
  assert.equal(supersededBy({ ...leftover, alive: true, index }), null);
});

test('only the registry definition status counts, and only parked', () => {
  for (const s of ['online', 'offline', 'spawning', 'retired', 'archived', 'provisional', '', undefined]) {
    assert.equal(supersededBy({ ...leftover, defStatus: s as any, index }), null, String(s));
  }
});

test('a row with no generation suffix is never flagged', () => {
  assert.equal(supersededBy({ id: 'devex-review', defStatus: 'parked', alive: false, index }), null);
});

// ---- fail-safe: the rule must NOT FIRE without registry evidence ---------------------------

test('no canonical index means the rule does not fire at all', () => {
  // Outside cutover, or on an unreadable DB, there is no condition 3 and no condition 5. The
  // rule must be SKIPPED, never degraded to conditions 1-2 — that would hide a row on its name
  // and status with no registry evidence whatsoever.
  assert.equal(buildCanonicalIndex(null), null);
  assert.equal(supersededBy({ ...leftover, index: null }), null);
});

test('an empty canonical map flags nothing', () => {
  const empty = buildCanonicalIndex({} as any)!;
  assert.equal(supersededBy({ ...leftover, index: empty }), null);
});

// ---- the index reads the DB values, not a row's own field -----------------------------------

test('the index carries both the roots and the canonical session names', () => {
  assert.ok(index.roots.has('gm') && index.roots.has('orchestraos-builder'));
  assert.ok(index.sessions.has('orchestraos-builder-g4'));
  // The DB-union step at agents.ts:96 overwrites a canonical root's tmux_session with the
  // canonical live head, so the rule must compare against THESE values, never the row's own.
  assert.ok(!index.roots.has('orchestraos-builder-g4'));
});
