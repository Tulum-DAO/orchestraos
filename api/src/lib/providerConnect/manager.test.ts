/**
 * ConnectManager over a fake tmux / exec / clock: no real install, sign-in or tmux session is ever touched.
 * Screens are the real captured ones (fixtures.ts).
 * Run: npx tsx --test src/lib/providerConnect/manager.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ConnectManager, type ConnectDeps, type PreflightIssue } from './manager.js';
import { signinScript, type ProviderSpec } from './specs.js';
import { CODEX_SIGNIN_PANE, GROK_SIGNIN_PANE, AGY_MENU_PANE, AGY_URL_PANE, AGY_URL_REBUILT, CODEX_INSTALL_LOG } from './fixtures.js';

const T0 = Date.parse('2026-11-04T08:00:00Z');

function fake(over: Partial<{ signedIn: boolean; installed: boolean; visible: boolean; preflight: PreflightIssue | null;
  install: { ok: boolean; log: string }; version: string | null; startOk: boolean; holdInstall: boolean }> = {}) {
  const st = {
    now: T0, signedIn: over.signedIn ?? false, installed: over.installed ?? true, visible: over.visible ?? true,
    preflight: over.preflight ?? null, install: over.install ?? { ok: true, log: CODEX_INSTALL_LOG },
    version: 'version' in over ? over.version! : '0.166.0', startOk: over.startOk ?? true,
  };
  const sessions = new Map<string, { pane: string; cwd: string; script: string; ours?: boolean }>();
  // holdInstall: the install stays running until release() — to race things against it.
  let release: () => void = () => {};
  const installGate = over.holdInstall ? new Promise<void>((r) => { release = r; }) : Promise.resolve();
  let installs = 0;
  const log: string[] = [];
  const preflightCalls: boolean[] = [];
  const deps: ConnectDeps = {
    now: () => st.now,
    tmux: {
      has: async (s) => sessions.has(s),
      start: async (s, cwd, script) => { log.push(`start ${s}`); if (!st.startOk) return false; sessions.set(s, { pane: '', cwd, script }); return true; },
      capture: async (s) => sessions.get(s)?.pane ?? '',
      sendLiteral: async (s, text) => { log.push(`literal ${s} ${text}`); },
      sendKey: async (s, key) => { log.push(`key ${s} ${key}`); },
      kill: async (s) => { log.push(`kill ${s}`); sessions.delete(s); },
      mark: async (s) => { log.push(`mark ${s}`); const x = sessions.get(s); if (x) x.ours = true; },
      isOurs: async (s) => !!sessions.get(s)?.ours,
    },
    signedIn: async () => st.signedIn,
    binInstalled: () => st.installed,
    visible: () => st.visible,
    preflight: async (_spec, o) => { preflightCalls.push(o.install); return st.preflight; },
    install: async (spec: ProviderSpec) => {
      installs += 1; log.push(`install ${spec.id}`);
      await installGate;
      if (st.install.ok) st.installed = true;
      return st.install;
    },
    ensureLink: (spec) => { log.push(`link ${spec.id}`); },
    version: async () => st.version,
    signinCwd: () => '/home/orchestra/.orchestra/signin',
    signinScript,
    refreshRuntimes: () => { log.push('refresh'); },
  };
  const m = new ConnectManager(deps);
  const pane = (s: string, text: string) => { const x = sessions.get(s); if (x) x.pane = text; };
  return { m, st, sessions, log, preflightCalls, pane, release: () => release(), installs: () => installs };
}

test('codex not installed: preflight, install, verify, then the device code with a 15-minute deadline', async () => {
  const f = fake({ installed: false });
  const v0 = f.m.start('codex');
  assert.equal(v0.phase, 'preflight');
  await f.m.settle();
  assert.deepEqual(f.preflightCalls, [true]);
  assert.ok(f.log.includes('install codex'));
  assert.equal(f.m.view('codex')!.phase, 'starting');
  assert.equal(f.m.view('codex')!.install, 'needed');
  const sess = f.sessions.get('login-codex')!;
  assert.match(sess.script, /codex login --device-auth; echo __OOS_EXIT=\$\?; sleep 1800$/);
  assert.ok(f.log.includes('mark login-codex'));   // ours: a later API process may sweep it
  f.st.now += 4000;
  f.pane('login-codex', CODEX_SIGNIN_PANE);
  await f.m.tick();
  const v = f.m.view('codex')!;
  assert.equal(v.phase, 'waiting');
  assert.equal(v.code, 'QWER-7TY2U');
  assert.equal(v.url, 'https://auth.openai.com/codex/device');
  assert.equal(v.expiresAt, T0 + 15 * 60_000);   // counted from when the CLI started, not from when we read it
  assert.equal(v.expiresInMs, 15 * 60_000 - 4000); // relative to the server's now: the client anchors it to its own clock
  assert.equal(v.version, '0.166.0');
});

test('already signed in: no install, no session, straight to signed in', async () => {
  const f = fake({ signedIn: true });
  f.m.start('grok');
  await f.m.settle();
  assert.equal(f.m.view('grok')!.phase, 'signed_in');
  assert.ok(!f.log.some((l) => l.startsWith('start') || l.startsWith('install')));
});

test('installed already: install skipped, preflight does not check disk/network', async () => {
  const f = fake();
  f.m.start('grok');
  await f.m.settle();
  assert.deepEqual(f.preflightCalls, [false]);
  assert.equal(f.m.view('grok')!.install, 'skipped');
  assert.ok(f.log.includes('link grok'));
});

test('a leftover login-<cli> session is killed before the sign-in starts', async () => {
  const f = fake();
  f.sessions.set('login-grok', { pane: 'sam@office-box:~$ ', cwd: '~', script: 'bash -i' });
  f.m.start('grok');
  await f.m.settle();
  assert.deepEqual(f.log.filter((l) => /login-grok/.test(l)).slice(0, 2), ['kill login-grok', 'start login-grok']);
});

test('reopening resumes the same attempt: same code, same timer, no second session', async () => {
  const f = fake();
  const first = f.m.start('grok');
  await f.m.settle();
  f.pane('login-grok', GROK_SIGNIN_PANE);
  await f.m.tick();
  const again = f.m.start('grok');
  assert.equal(again.attemptId, first.attemptId);
  assert.equal(again.code, 'AB12-CDEF');
  assert.equal(again.url, 'https://accounts.x.ai/oauth2/device?user_code=AB12-CDEF');
  assert.equal(again.expiresAt, null);    // grok prints no lifetime: no countdown
  assert.equal(f.log.filter((l) => l === 'start login-grok').length, 1);
});

test('codex expiry: a new code by itself, three times, then expired with Get a new code', async () => {
  const f = fake();
  f.m.start('codex');
  await f.m.settle();
  f.pane('login-codex', CODEX_SIGNIN_PANE);
  await f.m.tick();
  for (let i = 1; i <= 3; i += 1) {
    f.st.now += 15 * 60_000 + 1;
    f.m.view('codex');                 // the sheet is open and polling
    await f.m.tick();
    let v = f.m.view('codex')!;
    assert.equal(v.phase, 'starting');
    assert.equal(v.notice, 'renewed');
    assert.equal(v.code, null);                          // the expired code is never shown again
    f.pane('login-codex', CODEX_SIGNIN_PANE.replace('QWER-7TY2U', `NEWC-0000${i}`));
    await f.m.tick();
    v = f.m.view('codex')!;
    assert.equal(v.code, `NEWC-0000${i}`);
    assert.equal(v.renewals, i);
    assert.equal(v.notice, 'renewed');
  }
  f.st.now += 15 * 60_000 + 1;
  f.m.view('codex');
  await f.m.tick();
  const v = f.m.view('codex')!;
  assert.equal(v.phase, 'expired');
  assert.equal(f.sessions.has('login-codex'), false);
  // Get a new code = a fresh attempt.
  const again = f.m.start('codex', { fresh: true });
  assert.notEqual(again.attemptId, v.attemptId);
});

test('a CLI that ends while a code is out is renewed; one that ends before is an error', async () => {
  const f = fake();
  f.m.start('grok');
  await f.m.settle();
  f.pane('login-grok', GROK_SIGNIN_PANE);
  await f.m.tick();
  f.pane('login-grok', `${GROK_SIGNIN_PANE}\nError: device code expired\n__OOS_EXIT=1`);
  await f.m.tick();
  assert.equal(f.m.view('grok')!.phase, 'starting');
  assert.equal(f.m.view('grok')!.renewals, 1);

  const g = fake();
  g.m.start('codex');
  await g.m.settle();
  g.pane('login-codex', 'Error: something broke\n__OOS_EXIT=2');
  await g.m.tick();
  assert.deepEqual(g.m.view('codex')!.error, { code: 'cli_exited', detail: 'exit 2' });
  assert.equal(g.sessions.has('login-codex'), false);
  assert.match(await g.m.output('codex'), /something broke/);
});

test('codex device login switched off: its own error, in plain words on the sheet', async () => {
  const f = fake();
  f.m.start('codex');
  await f.m.settle();
  f.pane('login-codex', 'Error: device code login is not enabled for this workspace.\n__OOS_EXIT=1');
  await f.m.tick();
  assert.equal(f.m.view('codex')!.error?.code, 'device_auth_disabled');
});

test('signed in while waiting: the session goes, the runtimes are re-probed', async () => {
  const f = fake();
  f.m.start('codex');
  await f.m.settle();
  f.pane('login-codex', CODEX_SIGNIN_PANE);
  await f.m.tick();
  f.st.signedIn = true;
  f.st.now += 3001;
  await f.m.tick();
  assert.equal(f.m.view('codex')!.phase, 'signed_in');
  assert.equal(f.sessions.has('login-codex'), false);
  assert.ok(f.log.includes('refresh'));
});

test('install failures: EACCES is the permission error, anything else install_failed; preflight issues stop first', async () => {
  const f = fake({ installed: false, install: { ok: false, log: 'npm error code EACCES\nnpm error path /usr/lib/node_modules' } });
  f.m.start('codex');
  await f.m.settle();
  assert.equal(f.m.view('codex')!.error?.code, 'install_permission');
  assert.match(await f.m.output('codex'), /EACCES/);

  const g = fake({ installed: false, install: { ok: false, log: 'curl: (6) Could not resolve host' } });
  g.m.start('grok');
  await g.m.settle();
  assert.equal(g.m.view('grok')!.error?.code, 'install_failed');

  const h = fake({ installed: false, preflight: { code: 'preflight_network', host: 'x.ai' } });
  h.m.start('grok');
  await h.m.settle();
  assert.deepEqual(h.m.view('grok')!.error, { code: 'preflight_network', detail: 'x.ai' });
  assert.ok(!h.log.includes('install grok'));

  const k = fake({ installed: false, visible: false });
  k.m.start('grok');
  await k.m.settle();
  assert.equal(k.m.view('grok')!.error?.code, 'install_not_visible');
});

test('no sign-in screen within the start window: start_failed', async () => {
  const f = fake();
  f.m.start('grok');
  await f.m.settle();
  f.pane('login-grok', 'Checking for updates...');
  f.st.now += 46_000;
  await f.m.tick();
  assert.equal(f.m.view('grok')!.error?.code, 'start_failed');
});

test('agy: Enter on the Google OAuth menu once, then the rebuilt URL and the paste box', async () => {
  const f = fake();
  f.m.start('gemini');
  await f.m.settle();
  assert.equal(f.sessions.get('login-agy')!.cwd, '/home/orchestra/.orchestra/signin');
  f.pane('login-agy', AGY_MENU_PANE);
  await f.m.tick();
  await f.m.tick();
  assert.equal(f.log.filter((l) => l === 'key login-agy Enter').length, 1);
  f.pane('login-agy', AGY_URL_PANE);
  await f.m.tick();
  const v = f.m.view('gemini')!;
  assert.equal(v.phase, 'waiting');
  assert.equal(v.url, AGY_URL_REBUILT);
  assert.equal(v.needsCode, true);
  assert.equal(v.expiresAt, null);
});

test('agy code: strict charset, typed literally then Enter, token file → signed in', async () => {
  const f = fake();
  f.m.start('gemini');
  await f.m.settle();
  f.pane('login-agy', AGY_URL_PANE);
  await f.m.tick();
  assert.deepEqual(await f.m.submitCode('gemini', '4/0AV; rm -rf ~'), { ok: false, reason: 'bad_code' });
  assert.ok(!f.log.some((l) => l.startsWith('literal')));
  const r = await f.m.submitCode('gemini', ' 4/0AVG7fiQ_abcDEF-123 ');
  assert.equal(r.ok, true);
  assert.deepEqual(f.log.slice(-2), ['literal login-agy 4/0AVG7fiQ_abcDEF-123', 'key login-agy Enter']);
  assert.equal(f.m.view('gemini')!.phase, 'checking_code');
  f.st.signedIn = true;
  f.st.now += 1500;
  await f.m.tick();
  assert.equal(f.m.view('gemini')!.phase, 'signed_in');
});

test('agy wrong code: said so, the paste box comes back; a second refusal is noticed too', async () => {
  const f = fake();
  f.m.start('gemini');
  await f.m.settle();
  f.pane('login-agy', AGY_URL_PANE);
  await f.m.tick();
  await f.m.submitCode('gemini', '4/0AVG7fiQ_wrongcode');
  f.pane('login-agy', `${AGY_URL_PANE}\n Error: invalid authorization code`);
  f.st.now += 1500;
  await f.m.tick();
  let v = f.m.view('gemini')!;
  assert.equal(v.phase, 'waiting');
  assert.equal(v.codeError, 'code_rejected');
  await f.m.submitCode('gemini', '4/0AVG7fiQ_wrongagain');
  assert.equal(f.m.view('gemini')!.codeError, null);
  f.st.now += 1500;
  await f.m.tick();
  assert.equal(f.m.view('gemini')!.phase, 'checking_code');   // the old error line alone is not a new refusal
  f.pane('login-agy', `${AGY_URL_PANE}\n Error: invalid authorization code\n Error: invalid authorization code`);
  await f.m.tick();
  v = f.m.view('gemini')!;
  assert.equal(v.codeError, 'code_rejected');
});

test('agy: a code that neither works nor fails within 30 s is unconfirmed; agy quitting on a code gives a new link', async () => {
  const f = fake();
  f.m.start('gemini');
  await f.m.settle();
  f.pane('login-agy', AGY_URL_PANE);
  await f.m.tick();
  await f.m.submitCode('gemini', '4/0AVG7fiQ_maybe');
  f.st.now += 31_000;
  await f.m.tick();
  assert.equal(f.m.view('gemini')!.codeError, 'code_unconfirmed');

  await f.m.submitCode('gemini', '4/0AVG7fiQ_maybe2');
  f.pane('login-agy', `${AGY_URL_PANE}\n__OOS_EXIT=1`);
  await f.m.tick();
  const v = f.m.view('gemini')!;
  assert.equal(v.phase, 'starting');
  assert.equal(v.notice, 'code_renewed');
  assert.equal(v.codeError, 'code_rejected');
  assert.equal(v.url, null);
});

test('submitting a code is refused unless agy is waiting for one', async () => {
  const f = fake();
  assert.deepEqual(await f.m.submitCode('gemini', '4/0AVG7fiQ_abc'), { ok: false, reason: 'not_waiting' });
  f.m.start('codex');
  await f.m.settle();
  assert.deepEqual(await f.m.submitCode('codex', '4/0AVG7fiQ_abc'), { ok: false, reason: 'not_waiting' });
});

test('cancel kills the session and forgets the attempt', async () => {
  const f = fake();
  f.m.start('grok');
  await f.m.settle();
  assert.equal(await f.m.cancel('grok'), 'cancelled');
  assert.equal(f.sessions.has('login-grok'), false);
  assert.equal(f.m.view('grok'), null);
});

test('an attempt nobody watches is cleaned up after 10 minutes; ended ones are forgotten later', async () => {
  const f = fake();
  f.m.start('grok');
  await f.m.settle();
  f.pane('login-grok', GROK_SIGNIN_PANE);
  await f.m.tick();
  f.st.now += 5 * 60_000;
  f.m.view('grok');                 // watched: stays
  f.st.now += 9 * 60_000;
  await f.m.tick();
  assert.ok(f.sessions.has('login-grok'));
  f.st.now += 61_000 + 60_000;
  await f.m.tick();
  assert.equal(f.sessions.has('login-grok'), false);
  assert.equal(f.m.active, false);

  const g = fake({ signedIn: true });
  g.m.start('codex');
  await g.m.settle();
  g.st.now += 11 * 60_000;
  await g.m.tick();
  assert.equal(g.m.view('codex'), null);
});

test('Try again (fresh) replaces a failed attempt and starts over', async () => {
  const f = fake({ startOk: false });
  f.m.start('grok');
  await f.m.settle();
  assert.equal(f.m.view('grok')!.error?.code, 'start_failed');
  f.st.startOk = true;
  const v = f.m.start('grok');          // an ended attempt is never resumed
  await f.m.settle();
  assert.equal(v.phase, 'preflight');
  assert.equal(f.m.view('grok')!.phase, 'starting');
});

test('Cancel during an install is refused; the install finishes; Cancel works once sign-in starts', async () => {
  const f = fake({ installed: false, holdInstall: true });
  f.m.start('codex');
  await new Promise((r) => setImmediate(r));
  assert.equal(f.m.view('codex')!.phase, 'installing');
  assert.equal(await f.m.cancel('codex'), 'installing');
  assert.equal(f.m.view('codex')!.phase, 'installing');
  f.release();
  await f.m.settle();
  assert.equal(f.m.view('codex')!.phase, 'starting');
  assert.equal(await f.m.cancel('codex'), 'cancelled');
  assert.equal(f.installs(), 1);
});

test('Try again (fresh) during an install joins the running install, never a second one', async () => {
  const f = fake({ installed: false, holdInstall: true });
  const a = f.m.start('grok');
  await new Promise((r) => setImmediate(r));
  const b = f.m.start('grok', { fresh: true });
  assert.notEqual(b.attemptId, a.attemptId);
  await new Promise((r) => setImmediate(r));
  assert.equal(f.m.view('grok')!.phase, 'installing');
  assert.equal(f.m.installing('grok'), true);
  f.release();
  await f.m.settle();
  assert.equal(f.installs(), 1);
  assert.equal(f.m.view('grok')!.phase, 'starting');
  assert.equal(f.log.filter((l) => l === 'start login-grok').length, 1);   // only the live attempt signs in
  assert.equal(f.m.installing('grok'), false);
});

test('two tabs racing on Try again after a failure: one install', async () => {
  const f = fake({ installed: false, holdInstall: true, preflight: { code: 'preflight_network', host: 'x.ai' } });
  f.m.start('grok');
  await f.m.settle();
  assert.equal(f.m.view('grok')!.error?.code, 'preflight_network');
  f.st.preflight = null;
  const one = f.m.start('grok', { fresh: true });
  const two = f.m.start('grok', { fresh: true });
  assert.notEqual(one.attemptId, two.attemptId);
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  f.release();
  await f.m.settle();
  assert.equal(f.installs(), 1);
  assert.equal(f.m.view('grok')!.attemptId, two.attemptId);
  assert.equal(f.m.view('grok')!.phase, 'starting');
});

test('an attempt abandoned mid-install: the install is not cut short and nothing signs in afterwards', async () => {
  const f = fake({ installed: false, holdInstall: true });
  f.m.start('gemini');
  await new Promise((r) => setImmediate(r));
  f.st.now += 11 * 60_000;
  await f.m.tick();
  assert.equal(f.m.view('gemini'), null);
  assert.equal(f.m.installing('gemini'), true);
  f.release();
  await f.m.settle();
  assert.equal(f.installs(), 1);
  assert.ok(!f.log.includes('start login-agy'));
});

test('already installed but not on the API PATH: install_not_visible, after linking grok', async () => {
  const f = fake({ visible: false });
  f.m.start('grok');
  await f.m.settle();
  assert.ok(f.log.includes('link grok'));
  assert.equal(f.m.view('grok')!.error?.code, 'install_not_visible');
  assert.ok(!f.log.includes('start login-grok'));
});

test('API start: only the sessions this flow marked, with no attempt, are swept', async () => {
  const f = fake();
  f.sessions.set('login-codex', { pane: '', cwd: '~', script: 'codex login', ours: true });
  f.sessions.set('login-grok', { pane: '', cwd: '~', script: 'bash -i' });          // old flow / by hand: left alone
  assert.deepEqual(await f.m.sweepOrphans(), ['login-codex']);
  assert.ok(f.sessions.has('login-grok'));
  f.m.start('gemini');
  await f.m.settle();
  assert.deepEqual(await f.m.sweepOrphans(), []);                                     // a live attempt's session stays
  assert.ok(f.sessions.has('login-agy'));
});
