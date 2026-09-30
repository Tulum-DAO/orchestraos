/**
 * Regression guard for FIX 3 (BLOCKER) — duplicate sibling `key` in a component.
 *
 * THE BUG THIS EXISTS FOR. AgentDetailPanel.tsx rendered:
 *
 *     <DetailLiveFeed  key={agent.id} agentId={agent.id} />
 *     <MessageComposer key={agent.id} agentId={agent.id} disabled={isDown} />
 *
 * Two SIBLINGS with the SAME key. React's reconciler hits a key collision and
 * can keep the stale DetailLiveFeed mounted across an agent switch, so the
 * es.close() disposer in transcriptStream.ts never runs. One EventSource leaks
 * per switch; six switches exhaust the browser's per-origin connection pool and
 * the page stops fetching entirely (a plain fetch('/api/agents') from the page
 * timed out after 6002ms in review's repro).
 *
 * WHY A SOURCE CHECK AND NOT A MOUNT TEST.
 * ponytail: this asserts the source invariant, not the runtime unmount. The
 * dashboard has no DOM test harness — no jsdom, no @testing-library, and the
 * npm test script is plain node over src/lib. Adding one is a tooling change
 * this branch forbids riding along. The ceiling is real: this catches the key
 * collision, it does NOT prove the disposer fired. Upgrade path: jsdom +
 * @testing-library/react, then mount the panel, switch agentId N times and
 * assert exactly one open subscription — then this file can go. Proving the
 * disposer actually runs is review's live gate, not this test's job.
 *
 * Sibling detection uses the TypeScript parser that is already a devDependency
 * — a regex over indentation flagged nine separate `.map()` blocks as siblings.
 * Real siblings are direct JSX children of one parent; elements inside a
 * `{list.map(...)}` expression are children of that expression, not of the
 * parent, so two different maps reusing `key={i}` are correctly left alone.
 *
 *   node --experimental-strip-types dashboard/src/lib/siblingKeys.test.mjs
 */
import assert from 'node:assert';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

function tsxFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return tsxFiles(p);
    return name.endsWith('.tsx') ? [p] : [];
  });
}

const isElement = (n) =>
  ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n);

/** The `key={...}` attribute's source text, or null if the element has no key. */
function keyOf(node) {
  const opening = ts.isJsxSelfClosingElement(node) ? node : node.openingElement;
  if (!opening?.attributes) return null;
  for (const attr of opening.attributes.properties) {
    if (ts.isJsxAttribute(attr) && attr.name.getText() === 'key') {
      return attr.initializer ? attr.initializer.getText() : '';
    }
  }
  return null;
}

/**
 * Every set of DIRECT JSX children of one parent that share a key.
 * Returns [{ key, lines: [1-indexed, ...] }].
 */
export function findDuplicateSiblingKeys(source, fileName = 'x.tsx') {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.ESNext, true, ts.ScriptKind.TSX);
  const dupes = [];

  const lineOf = (n) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;

  const checkChildren = (children) => {
    const byKey = new Map();
    for (const child of children) {
      if (!isElement(child)) continue;
      const key = keyOf(child);
      if (key === null) continue;
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(lineOf(child));
    }
    for (const [key, lines] of byKey) {
      if (lines.length > 1) dupes.push({ key, lines });
    }
  };

  const walk = (node) => {
    if (ts.isJsxElement(node) || ts.isJsxFragment(node)) checkChildren(node.children);
    ts.forEachChild(node, walk);
  };
  walk(sf);

  return dupes;
}

// --- the checker itself must be able to see the bug ------------------------
// Without this, a checker that always returns [] would sail through the sweep
// below and the guard would be worth nothing.
{
  const buggy = `const C = () => (
  <div>
    <DetailLiveFeed key={agent.id} agentId={agent.id} />
    <MessageComposer key={agent.id} agentId={agent.id} disabled={isDown} />
  </div>
);`;
  const found = findDuplicateSiblingKeys(buggy);
  assert.strictEqual(found.length, 1, 'checker must flag the original AgentDetailPanel shape');
  assert.strictEqual(found[0].key, '{agent.id}');
  assert.deepStrictEqual(found[0].lines, [3, 4]);
  console.log('PASS: checker detects the exact collision that shipped');
}

// --- and must not cry wolf --------------------------------------------------
{
  const fixed = `const C = () => (
  <div>
    <DetailLiveFeed key={\`feed-\${agent.id}\`} agentId={agent.id} />
    <MessageComposer key={\`composer-\${agent.id}\`} agentId={agent.id} disabled={isDown} />
  </div>
);`;
  assert.deepStrictEqual(findDuplicateSiblingKeys(fixed), [], 'distinct keys are fine');

  // Two separate .map() blocks reusing the same key name are NOT siblings —
  // this is the case the indentation heuristic got wrong nine times over.
  const twoMaps = `const C = () => (
  <div>
    <div>{tags.map((t) => <Chip key={t} label={t} />)}</div>
    <div>{names.map((t) => <Chip key={t} label={t} />)}</div>
  </div>
);`;
  assert.deepStrictEqual(findDuplicateSiblingKeys(twoMaps), [], 'separate maps are separate parents');

  // Parent and child sharing a key expression is legal — keys are scoped to siblings.
  const nested = `const C = () => (
  <Row key={a.id}>
    <Cell key={a.id} />
  </Row>
);`;
  assert.deepStrictEqual(findDuplicateSiblingKeys(nested), [], 'parent/child is not a collision');

  // Siblings far apart in the same parent ARE still a collision — distance is
  // irrelevant to the reconciler, and the old heuristic missed exactly this.
  const farApart = `const C = () => (
  <div>
    <A key={x.id} />
    <p>spacer</p>
    <p>spacer</p>
    <p>spacer</p>
    <p>spacer</p>
    <p>spacer</p>
    <p>spacer</p>
    <B key={x.id} />
  </div>
);`;
  assert.strictEqual(findDuplicateSiblingKeys(farApart).length, 1, 'distance does not make it safe');

  console.log('PASS: checker clears distinct keys, separate maps and nesting — and still sees distant siblings');
}

// --- the actual sweep: no component may ship a sibling key collision -------
// MUTATION CHECK: restore key={agent.id} on both lines in AgentDetailPanel.tsx
// and this block goes red naming that file and those two line numbers.
{
  const files = tsxFiles(SRC);
  const offenders = [];
  for (const file of files) {
    for (const d of findDuplicateSiblingKeys(readFileSync(file, 'utf-8'), file)) {
      offenders.push(`${relative(SRC, file)}:${d.lines.join(',')} all key=${d.key}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'duplicate sibling keys leak subscriptions on remount:\n  ' + offenders.join('\n  '));
  console.log(`PASS: no duplicate sibling keys across ${files.length} .tsx files`);
}

console.log('\nAll siblingKeys tests passed.');
