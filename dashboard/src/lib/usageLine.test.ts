/**
 * The sidebar has no plan-usage block: nothing under the operator row. The usage API and its query stay
 * for the model picker's rings.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');

test('the sidebar renders no Plan usage block, in either variant (desktop aside and phone drawer)', () => {
  const side = read('components/Sidebar.tsx');
  assert.doesNotMatch(side, /UsageLine/);
  assert.doesNotMatch(side, /Plan usage/);
  assert.doesNotMatch(side, /<div className="pt-2">\s*<\/div>/);   // no empty gap left behind
  assert.equal(existsSync(join(SRC, 'components/workbench/UsageLine.tsx')), false);
});

test('the model picker still reads usage through the shared query', () => {
  const picker = read('components/kit/ModelPicker.tsx');
  assert.match(picker, /fetchUsage, USAGE_QUERY_KEY, UsageHidden/);
});
