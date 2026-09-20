// RED-first (#85): the voice routes must read the gateway bearer from WATCH_GATEWAY_TOKEN_FILE
// (what `orchestra init` writes + exports), falling back to the operator's legacy path only when
// the env var is unset. voice.ts hard-coded the legacy path, so every public install's voice-call
// card rendered "transcript unavailable".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { gatewayTokenFile, readGatewayToken } from './gateway-token.js';

test('token file comes from WATCH_GATEWAY_TOKEN_FILE when set', () => {
  const dir = mkdtempSync(join(tmpdir(), 'gwtok-'));
  const file = join(dir, 'watch-gateway-token');
  writeFileSync(file, 'sekrit\n');
  process.env.WATCH_GATEWAY_TOKEN_FILE = file;
  assert.equal(gatewayTokenFile(), file);
  assert.equal(readGatewayToken(), 'sekrit');
});

test('falls back to the legacy ~/.config/jarvis path when unset', () => {
  delete process.env.WATCH_GATEWAY_TOKEN_FILE;
  assert.equal(gatewayTokenFile(), `${process.env.HOME}/.config/jarvis/watch-gateway-token`);
});

test('missing token file reads as empty, never throws', () => {
  process.env.WATCH_GATEWAY_TOKEN_FILE = '/nonexistent/watch-gateway-token';
  assert.equal(readGatewayToken(), '');
});
