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

// #85: this asserted env -> LEGACY, which is the ordering that left voice dead on every fresh
// install — `orchestra init` writes the bearer to <data dir>/state/watch-gateway-token and the
// reader never looked there. Order is now env -> CONFIGURED -> legacy.
//
// THE LEGACY BRANCH IS NOT EXERCISED HERE AND I AM SAYING SO RATHER THAN FAKING IT: loadConfig
// resolves orchestra.toml from the MODULE's own location, not the cwd, so it cannot be made to
// fail from inside a checkout by chdir'ing. I tried exactly that and the assertion failed
// because the config was still found. The branch is reachable only on an install with no
// orchestra.toml at all, which this suite cannot construct.
test('with the env unset, the CONFIGURED data dir wins over the legacy path', () => {
  delete process.env.WATCH_GATEWAY_TOKEN_FILE;
  const f = gatewayTokenFile();
  assert.ok(f.endsWith('/state/watch-gateway-token'), f);
  assert.ok(!f.includes('.config/jarvis'), f);
});

test('missing token file reads as empty, never throws', () => {
  process.env.WATCH_GATEWAY_TOKEN_FILE = '/nonexistent/watch-gateway-token';
  assert.equal(readGatewayToken(), '');
});

// #85: on a FRESH install nothing exports WATCH_GATEWAY_TOKEN_FILE, and `orchestra init` writes
// the bearer to <data dir>/state/watch-gateway-token. Falling straight back to the legacy
// ~/.config/jarvis path means voice is dead out of the box on every new install — which is
// exactly what check-voice-path.sh reports as "missing/empty ... 'orchestra init' writes it".
test('prefers the CONFIGURED data dir over the legacy path when the env is unset', () => {
  delete process.env.WATCH_GATEWAY_TOKEN_FILE;
  const f = gatewayTokenFile();
  assert.ok(!f.includes('.config/jarvis'),
    `fresh install fell back to the legacy path: ${f}`);
  assert.ok(f.endsWith('/state/watch-gateway-token'), f);
});
