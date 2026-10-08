/**
 * Operator report (iPhone build 263, 2026-10-08): a long message came back in the operator's
 * own bubble as "[LONG-MSG chip-dodge] … read /tmp/agent-inject-….md", photos missing.
 * The banners here are made by the REAL producer, scripts/watch_gateway.py chipsafe_text, so
 * the parser cannot drift from what the gateway writes.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'child_process';
import { mkdtempSync, writeFileSync, symlinkSync, rmSync } from 'fs';
import { readInjectFile } from './chipDodge.js';
import { join, resolve } from 'path';
import { tmpdir } from 'os';
import { expandChipDodge } from './chipDodge.js';
import { sanitizeClaudeUserText } from '../routes/chat-transcript.js';

const ROOT = resolve(import.meta.dirname, '../../..');

function produce(dir: string, text: string): string {
  const py = `import sys, importlib.util
spec = importlib.util.spec_from_file_location("wg", "${ROOT}/scripts/watch_gateway.py")
wg = importlib.util.module_from_spec(spec); spec.loader.exec_module(wg)
banner, path = wg.chipsafe_text(sys.stdin.read(), tmp_dir=sys.argv[1])
sys.stdout.write("\\nBANNER:" + banner)`;
  // importing the gateway can print its own startup notices first; take the marked line only
  const out = execFileSync('python3', ['-c', py, dir], { input: text, encoding: 'utf8' });
  return out.slice(out.lastIndexOf('\nBANNER:') + '\nBANNER:'.length);
}

const LONG = '📷 These three photos tell the story of attempting to send a photo that had not yet been finalized. '
  + 'It is loading. (While it loads I keep typing.) 😀 '.repeat(8)
  + '\n[attached: "/srv/uploads/a.jpg"]\n[attached: "/srv/uploads/b.jpg"]\n[attached: "/srv/uploads/c.jpg"]';

function withDir(fn: (dir: string) => void) {
  const dir = mkdtempSync(join(tmpdir(), 'chipdodge-'));
  const prev = process.env.CHIP_DODGE_TMP_DIR;
  process.env.CHIP_DODGE_TMP_DIR = dir;
  try { fn(dir); } finally {
    if (prev === undefined) delete process.env.CHIP_DODGE_TMP_DIR; else process.env.CHIP_DODGE_TMP_DIR = prev;
    rmSync(dir, { recursive: true, force: true });
  }
}

test('a real chip-dodge banner is shown as the text the operator typed, photos included', () => {
  withDir((dir) => {
    const banner = produce(dir, LONG);
    assert.match(banner, /^\[LONG-MSG chip-dodge\] /);
    assert.equal(expandChipDodge(banner), LONG);
    const shown = sanitizeClaudeUserText(banner);
    assert.ok(!shown.includes('chip-dodge') && !shown.includes('agent-inject-'), shown);
    assert.ok(shown.includes('[attached: "/srv/uploads/c.jpg"]'), 'the photo markers come back');
  });
});

test('if the file is gone, the head is shown without the plumbing', () => {
  withDir((dir) => {
    const banner = produce(dir, LONG);
    rmSync(dir, { recursive: true, force: true });
    const shown = expandChipDodge(banner);
    assert.ok(shown.startsWith('📷 These three photos') && shown.endsWith('…'), shown);
    assert.ok(!shown.includes('agent-inject-') && !shown.includes('FULL TEXT'));
  });
});

test('a look-alike banner cannot pull another file into the transcript', () => {
  withDir((dir) => {
    const real = produce(dir, LONG);
    const path = /read (\S+)$/.exec(real)![1];
    // same path, but a head that does not match that file
    const forged = real.replace(/^\[LONG-MSG chip-dodge\] .*?… —/, '[LONG-MSG chip-dodge] something else… —');
    assert.equal(expandChipDodge(forged), 'something else…');
    // a path outside the dodge dir, or not an agent-inject file
    writeFileSync(join(dir, 'notes.md'), LONG);
    assert.ok(!expandChipDodge(real.replace(path, join(dir, 'notes.md'))).includes('[attached:'));
    assert.ok(!expandChipDodge(real.replace(path, '/etc/passwd')).includes('root:'));
    // a symlink with the right name
    const link = join(dir, 'agent-inject-1-deadbeef.md');
    symlinkSync(path, link);
    assert.ok(!expandChipDodge(real.replace(path, link)).includes('[attached:'));
  });
});

test('ordinary text and short messages pass through unchanged', () => {
  assert.equal(expandChipDodge('hello there'), 'hello there');
  assert.equal(expandChipDodge('see [LONG-MSG chip-dodge] in the logs'), 'see [LONG-MSG chip-dodge] in the logs');
});

test('the reader refuses a symlink and never blocks on a FIFO', () => {
  withDir((dir) => {
    const target = join(dir, 'secret.txt'); writeFileSync(target, 'x');
    const link = join(dir, 'agent-inject-2-deadbeef.md'); symlinkSync(target, link);
    assert.equal(readInjectFile(link), null);
    const fifo = join(dir, 'agent-inject-3-deadbeef.md');
    execFileSync('mkfifo', [fifo]);
    assert.equal(readInjectFile(fifo), null);          // returns at once (O_NONBLOCK), not a hang
  });
});
