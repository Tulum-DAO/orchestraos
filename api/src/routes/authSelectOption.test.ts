/**
 * POST /api/system/vps-auth/select-option pasted req.body.key into a `bash -c` string (shell injection).
 * The REAL router runs with a FAKE `bash` first on PATH that only records its argv, so neither this
 * test nor a sabotaged run can ever press a key into a real tmux pane.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, chmodSync, existsSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

test('select-option: an injected key is refused 400 and nothing is executed; a menu key is accepted', async () => {
  const bin = mkdtempSync(join(tmpdir(), 'fakebin-'));
  const log = join(bin, 'calls.log');
  for (const name of ['bash', 'ssh', 'tmux']) {
    writeFileSync(join(bin, name), `#!/bin/sh\necho "${name} $*" >> ${log}\n`);
    chmodSync(join(bin, name), 0o755);
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${bin}:${oldPath}`;
  if (!process.env.ORCHESTRA_CONFIG) {
    process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
  }
  const express = (await import('express')).default;
  const { default: auth, SELECT_OPTION_KEY } = await import(`./auth.js?t=${Date.now()}`);
  const a = express();
  a.use(express.json());
  a.use('/api/system/vps-auth', auth);
  const server = a.listen(0);
  const base = `http://127.0.0.1:${(server.address() as any).port}`;
  const post = (body: unknown) => fetch(base + '/api/system/vps-auth/select-option', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  try {
    for (const bad of ["'; id; '", "x' Enter; touch /tmp/pwn; '", 'Enter Enter', '$(id)', 12, ['1']]) {
      const r = await post({ key: bad });
      assert.equal(r.status, 400, JSON.stringify(bad));
    }
    assert.ok(!existsSync(log), 'a refused key must execute NOTHING: ' + (existsSync(log) ? readFileSync(log, 'utf-8') : ''));
    for (const ok of ['1', 'Enter', 'Down']) assert.ok(SELECT_OPTION_KEY.test(ok), ok);
    assert.equal((await post({ key: '2' })).status, 200);          // runs the FAKE bash only
    assert.match(readFileSync(log, 'utf-8'), /tmux send-keys -t gm '2' Enter/);
  } finally {
    server.close();
    process.env.PATH = oldPath;
  }
});
