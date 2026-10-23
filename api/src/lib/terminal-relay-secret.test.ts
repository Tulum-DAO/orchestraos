import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const dir = mkdtempSync(join(tmpdir(), 'orchestra-terminal-relay-'));
const secretFile = join(dir, 'secret');
writeFileSync(secretFile, 'relay-test-secret\n');
process.env.TERMINAL_RELAY_SECRET_FILE = secretFile;

function req(value?: string) {
  return { headers: value === undefined ? {} : { 'x-orchestra-terminal-relay': value } };
}

test('terminal relay credential accepts only an exact reference-held value', async () => {
  const { isTerminalRelay } = await import('./terminal-relay-secret.js');
  assert.equal(isTerminalRelay(req('relay-test-secret')), true);
  assert.equal(isTerminalRelay(req()), false);
  assert.equal(isTerminalRelay(req('wrong')), false);
  assert.equal(isTerminalRelay(req('relay-test-secret-x')), false);
});
