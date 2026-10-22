import { test } from 'node:test';
import assert from 'node:assert/strict';
import { exportUrl, exportFileName } from './exportMarkdown.ts';

test('the export URL is the agent route, options only when asked', () => {
  assert.equal(exportUrl('gm'), '/api/agents/gm/transcript.md');
  assert.equal(exportUrl('a b', { last: 10, tools: 'full' }), '/api/agents/a%20b/transcript.md?last=10&tools=full');
});

test('the file name comes from the server, never a path', () => {
  assert.equal(exportFileName('attachment; filename="gm-2026-10-22.md"', 'gm'), 'gm-2026-10-22.md');
  assert.equal(exportFileName(null, 'gm'), 'gm.md');
  assert.equal(exportFileName('attachment; filename="../../etc/passwd"', 'gm'), 'passwd');
});
