/**
 * Temp dirs for tests that are removed when the test process exits.
 *
 * Tests used to call mkdtempSync(join(tmpdir(), 'x-')) and never remove the dir. Gate runs in
 * many worktrees left tens of thousands of them in /tmp, enough to fill a root disk. Use
 * testTmpDir('x-') instead: same dir, but it is deleted on process exit (node:test runs each
 * file in its own process, so that is the end of the file). Removal is best effort and never
 * throws, so a test that already removed its dir, or still holds a file open, is fine.
 * Set ORCHESTRA_KEEP_TEST_TMP=1 to keep the dirs for debugging.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const made: string[] = [];
let hooked = false;

export function cleanupTestTmpDirs(): void {
  if (process.env.ORCHESTRA_KEEP_TEST_TMP === '1') return;
  while (made.length) {
    const d = made.pop()!;
    try { rmSync(d, { recursive: true, force: true }); } catch { /* best effort */ }
  }
}

export function testTmpDir(prefix: string): string {
  const d = mkdtempSync(join(tmpdir(), prefix));
  made.push(d);
  if (!hooked) {
    hooked = true;
    process.on('exit', cleanupTestTmpDirs);
  }
  return d;
}
