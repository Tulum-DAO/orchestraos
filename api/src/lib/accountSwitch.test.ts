/**
 * "Use this account for all agents" (lib/accountSwitch.ts). Scratch dirs only: a fake HOME whose .claude is the
 * fleet dir, a fake ORCHESTRA_DIR, FAKE logins (sentinel strings, never a real token), a fake tmux on PATH.
 * The end-to-end cases run the REAL switch (agent-orchestra scripts/account_switch.py, ACCOUNT_SWITCH_CLI or the
 * default path) against those dirs, exactly as the route runs it.
 * Run: npx tsx --test src/lib/accountSwitch.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, type ChildProcess } from 'node:child_process';
import {
  existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, statSync, symlinkSync, writeFileSync,
} from 'node:fs';
import { testTmpDir } from './testTmp.js';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  CODEX_TERMS_LINE, codexAccountsView, codexUsageNote, codexRefusalWords, loadCodexAccountsView, TERMS_LINE,
  accountsView, displacedWords, displayName, loadAccountsView, refusalWords, recordSwitchBy, shawZone, timeText, parseCliLine, readHolders, recordTermsAck, runCli, switchAllAgents, switchAnswer,
  termsAcknowledged, type CliResult, type RunCli,
} from './accountSwitch.js';

const CLI = process.env.ACCOUNT_SWITCH_CLI || join(homedir(), 'scripts', 'agent-orchestra', 'scripts', 'account_switch.py');

function login(tag: string, rt = `RT-${tag}-SENTINEL`) {
  return { claudeAiOauth: { accessToken: `AT-${tag}-SENTINEL`, refreshToken: rt, expiresAt: Date.now() + 3_600_000, subscriptionType: tag } };
}
function writeLogin(dir: string, doc: unknown) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, '.credentials.json'), JSON.stringify(doc), { mode: 0o600 });
}

class Host {
  root = testTmpDir('acct-l1-');
  home = join(this.root, 'home');
  od = join(this.root, 'orchestra');
  fleet = join(this.home, '.claude');
  beta = join(this.home, '.claude-beta');
  bin = join(this.root, 'bin');
  kids: ChildProcess[] = [];
  constructor(accounts: Record<string, string> | null = null) {
    mkdirSync(this.fleet, { recursive: true }); mkdirSync(this.beta, { recursive: true });
    mkdirSync(join(this.od, 'state'), { recursive: true }); mkdirSync(this.bin, { recursive: true });
    const reg = accounts ?? { default: this.fleet, beta: this.beta };
    writeFileSync(join(this.od, 'state', 'claude_profiles.json'), JSON.stringify({ active: 'default', profiles: [
      ...Object.entries(reg).map(([id, d]) => ({ id, config_dir: d, enrolled: true,
        note: id === 'default' ? 'agent login shaw.cole@bizypro.co' : 'owner@strategysimple.com' })),
      { id: 'codexwork', provider: 'codex', config_dir: join(this.home, '.codex'), enrolled: true },
    ] }));
    // a fake tmux: the switch lists panes to re-key the meter; nothing here may touch the real server
    writeFileSync(join(this.bin, 'tmux'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  }
  /** The real CLI, run the way the route runs it, with this scratch host's HOME and fake tmux. */
  run: RunCli = (py, args, env, t) => runCli(py, args, { ...env, HOME: this.home, PATH: `${this.bin}:${env.PATH}` }, t);
  switchTo(account: string) {
    return switchAllAgents(account, { dataDir: this.od, run: this.run, cli: CLI, names: { default: 'bizypro', beta: 'strategysimple' } });
  }
  /** {tag: [files holding its refresh token]} over the whole scratch host (links skipped). */
  copies(): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    const walk = (d: string) => {
      for (const n of readdirSync(d)) {
        const p = join(d, n);
        const st = lstatSync(p);
        if (st.isSymbolicLink()) continue;
        if (st.isDirectory()) { walk(p); continue; }
        const txt = readFileSync(p, 'utf8');
        for (const m of txt.matchAll(/RT-([a-z0-9]+)-SENTINEL/g)) (out[m[1]] ??= []).includes(p) || out[m[1]].push(p);
      }
    };
    walk(this.root);
    return out;
  }
  /** Every login file's bytes (links as their target), plus the ledger's meaning (rows + pending). */
  snapshot(): Record<string, string> {
    const out: Record<string, string> = {};
    const walk = (d: string) => {
      for (const n of readdirSync(d)) {
        const p = join(d, n);
        const st = lstatSync(p);
        if (st.isDirectory() && !st.isSymbolicLink()) { walk(p); continue; }
        if (n !== '.credentials.json') continue;
        out[p] = st.isSymbolicLink() ? `link:${p}` : readFileSync(p, 'utf8');
      }
    };
    walk(this.root);
    let led: any = {};
    try { led = JSON.parse(readFileSync(join(this.od, 'state', 'account_locations.json'), 'utf8')); } catch { /* none */ }
    out.ledger = JSON.stringify({ accounts: led.accounts ?? {}, pending: led.pending ?? null });
    return out;
  }
  /** A process the switch counts as Claude on `dir` (argv[0] "claude", CLAUDE_CONFIG_DIR=dir). */
  claudeOn(dir: string): number {
    const k = spawn('/bin/sleep', ['30'], { argv0: 'claude', env: { CLAUDE_CONFIG_DIR: dir }, stdio: 'ignore' });
    this.kids.push(k);
    return k.pid!;
  }
  done() { for (const k of this.kids) k.kill('SIGKILL'); }
}

const whoAt = (dir: string) => JSON.parse(readFileSync(join(dir, '.credentials.json'), 'utf8')).claudeAiOauth?.subscriptionType ?? null;
const cliHere = existsSync(CLI);

// ---- the cards' state ------------------------------------------------------------------------------

const H = (over: Record<string, object> = {}) => ({
  default: { where: 'shared', seat: null, path: '/x/.claude/.credentials.json', live_pids: [11, 12] },
  beta: { where: 'home', seat: null, path: '/x/.claude-beta/.credentials.json', live_pids: [] },
  ...over,
});

test('one Claude account (Codex does not count): no cards, the page stays as it is; the switch is never asked', async () => {
  const h = new Host({ default: join(tmpdir(), 'x', '.claude') });
  assert.equal(accountsView({ dataDir: h.od, home: h.home, holders: H() }), null);
  let asked = 0;
  const run: RunCli = async () => { asked++; return { code: 0, stdout: '{}', timedOut: false }; };
  assert.equal(await loadAccountsView({ dataDir: h.od, home: h.home, run }), null);
  assert.equal(asked, 0);
});

test('the cards come from --holders: the fleet login is "all agents", a free home login can be chosen, no address', () => {
  const h = new Host();
  writeLogin(h.beta, login('beta'));
  const v = accountsView({ dataDir: h.od, home: h.home, holders: H() })!;
  assert.equal(v.shared, 'default');
  assert.equal(v.followers, 2);
  const [d, b] = v.accounts;
  assert.deepEqual([d.id, d.where, d.agents, d.canUse, d.name], ['default', 'shared', true, false, 'bizypro']);
  assert.deepEqual([b.id, b.where, b.agents, b.canUse, b.whyNot, b.name], ['beta', 'home', false, true, null, 'strategysimple']);
  assert.deepEqual(v.accounts.map((a) => a.email), ['shaw.cole@bizypro.co', 'owner@strategysimple.com'], 'each card names its login');
  assert.ok(!JSON.stringify(v).includes('/x/'), 'no path in the view');
  assert.ok(!v.accounts.some((a) => a.id === 'codexwork'), 'no Codex card');
  assert.equal(displayName({ id: 'default', config_dir: '~/.claude' }), 'Main login');
});

test('a login signed in in another session is shown as not available, with the pid, never offered', () => {
  const h = new Host();
  writeLogin(h.beta, login('beta'));
  const b = accountsView({ dataDir: h.od, home: h.home, holders: H({ beta: { where: 'home', live_pids: [774272] } }) })!.accounts[1];
  assert.equal(b.canUse, false);
  assert.match(b.whyNot!, /another Claude session.*pid 774272.*never copied/);
});

test('no login in the home dir, a seat holding it, a half-finished switch or no --holders answer: not offered, each says why', () => {
  const h = new Host();
  assert.match(accountsView({ dataDir: h.od, home: h.home, holders: H() })!.accounts[1].whyNot!, /No login is in/);
  assert.match(accountsView({ dataDir: h.od, home: h.home, holders: H({ beta: { where: 'seat', seat: 'pm-x', live_pids: [7] } }) })!.accounts[1].whyNot!, /pm-x holds this login/);
  writeLogin(h.beta, login('beta'));
  const none = accountsView({ dataDir: h.od, home: h.home, holders: null })!;
  assert.deepEqual(none.accounts.map((a) => a.canUse), [false, false]);
  assert.equal(none.shared, null, 'never inferred');
  assert.match(none.accounts[1].whyNot!, /--holders did not answer/);
  writeFileSync(join(h.od, 'state', 'account_locations.json'), JSON.stringify({ accounts: {}, pending: { to: 'beta' } }));
  const b = accountsView({ dataDir: h.od, home: h.home, holders: H() })!.accounts[1];
  assert.equal(b.canUse, false);
  assert.match(b.whyNot!, /stopped half-way/);
});

test('after a switch: --holders names the shared account and the parked one can be chosen back', () => {
  const h = new Host();
  const v = accountsView({ dataDir: h.od, home: h.home, holders: H({
    default: { where: 'vault', live_pids: [] }, beta: { where: 'shared', live_pids: [1, 2, 3] } }) })!;
  assert.equal(v.shared, 'beta');
  assert.equal(v.followers, 3);
  assert.deepEqual(v.accounts.map((a) => [a.id, a.where, a.agents, a.canUse]), [['default', 'vault', false, true], ['beta', 'shared', true, false]]);
});

test('the top line: all agents\' login with its e-mail, when it was switched (Shaw\'s zone) and by whom', () => {
  const h = new Host();
  mkdirSync(join(h.od, 'logs'), { recursive: true });
  writeFileSync(join(h.od, 'logs', 'account-switch.jsonl'), [
    { ts: '2026-11-21T20:19:43+00:00', seat: '*shared*', to: 'beta', status: 'refused', reason: 'account-live-at-home' },
    { ts: '2026-11-21T20:23:13+00:00', seat: '*shared*', to: 'beta', status: 'switched', from: 'default', fp_to: 'ffffffffffff' },
  ].map((r) => JSON.stringify(r)).join('\n') + '\n');
  writeFileSync(join(h.od, 'state', 'shaw-location.json'), JSON.stringify({ tz: 'America/New_York', label: 'ET' }));
  const holders = H({ default: { where: 'vault', live_pids: [] }, beta: { where: 'shared', live_pids: [1] } });
  const now = new Date('2026-11-21T21:00:00Z');
  let v = accountsView({ dataDir: h.od, home: h.home, holders, now })!;
  assert.deepEqual(v.current, { account: 'beta', name: 'strategysimple', email: 'owner@strategysimple.com',
    since: '2026-11-21T20:23:13+00:00', since_text: '3:23 PM ET', by: 'hand (account_switch.py)' });
  assert.deepEqual(v.seats, {}, 'Level 1: no agent holds a login of its own');
  assert.deepEqual(accountsView({ dataDir: h.od, home: h.home, now, holders: H({ beta: { where: 'seat', seat: 'pm-x', live_pids: [] } }) })!.seats, { 'pm-x': 'beta' });
  assert.ok(!JSON.stringify(v).includes('ffffffffffff'), 'no fingerprint');
  recordSwitchBy(h.od, 'beta', 'shaw (dashboard)', new Date('2026-11-21T20:23:14Z'));
  v = accountsView({ dataDir: h.od, home: h.home, holders, now })!;
  assert.equal(v.current!.by, 'shaw (dashboard)');
  assert.equal(statSync(join(h.od, 'state', 'account_switch_by.json')).mode & 0o777, 0o600);
  assert.equal(timeText('2026-11-20T20:23:13Z', { tz: 'America/New_York', label: 'ET' }, now), 'Fri 3:23 PM ET', 'another day names it');
  const none = accountsView({ dataDir: h.od, home: h.home, holders: H(), now })!;
  assert.deepEqual([none.current!.account, none.current!.since, none.current!.by], ['default', null, null], 'never switched: no time');
  assert.equal(accountsView({ dataDir: h.od, home: h.home, holders: null, now })!.current, null);
  writeFileSync(join(h.od, 'state', 'shaw-location.json'), JSON.stringify({ tz: 'Not/AZone' }));
  assert.deepEqual(shawZone(h.od), { tz: 'America/Cancun', label: 'Tulum' }, 'a bad zone: his home base');
});

test('readHolders: the last JSON line, an object of objects; a failure, a timeout or garbage is null', async () => {
  const h = new Host();
  const r = (out: CliResult): RunCli => async () => out;
  assert.deepEqual(await readHolders({ dataDir: h.od, run: r({ code: 0, stdout: 'x\n{"a":{"where":"vault"}}\n', timedOut: false }) }), { a: { where: 'vault' } });
  assert.equal(await readHolders({ dataDir: h.od, run: r({ code: 2, stdout: '{"a":{}}', timedOut: false }) }), null);
  assert.equal(await readHolders({ dataDir: h.od, run: r({ code: null, stdout: '', timedOut: true }) }), null);
  assert.equal(await readHolders({ dataDir: h.od, run: r({ code: 0, stdout: '{"a":1}', timedOut: false }) }), null);
  let args: string[] = [];
  await readHolders({ dataDir: h.od, cli: '/x/account_switch.py', run: async (_p, a) => { args = a; return { code: 0, stdout: '{}', timedOut: false }; } });
  assert.deepEqual(args, ['/x/account_switch.py', '--holders']);
});

test('the terms acknowledgement is recorded once, 0600', () => {
  const h = new Host();
  assert.equal(termsAcknowledged(h.od), false);
  recordTermsAck(h.od, 'dashboard');
  assert.equal(termsAcknowledged(h.od), true);
  assert.equal(statSync(join(h.od, 'state', 'account_terms_ack.json')).mode & 0o777, 0o600);
});

// ---- the CLI's answer, in words -----------------------------------------------------------------------

const cli = (code: number | null, line: unknown, extra: Partial<CliResult> = {}): CliResult =>
  ({ code, stdout: `${JSON.stringify(line)}\n`, timedOut: false, ...extra });

test('switched / noop: ok, plain words; no fingerprint, path or detail is ever passed on', () => {
  const sw = switchAnswer('beta', cli(0, { status: 'switched', from: 'default', to: 'beta', followers: 52,
    fp_from: 'aaaaaaaaaaaa', fp_to: 'bbbbbbbbbbbb', seat_dir: '/home/x/.claude', inode_before: 1, inode_after: 2 }),
  { default: 'bizypro', beta: 'strategysimple' });
  assert.equal(sw.http, 200);
  assert.equal(sw.body.ok, true);
  assert.equal(sw.body.status, 'switched');
  assert.match(sw.body.message, /All agents on the main login \(52 Claude processes\) switch to strategysimple at their next model call\. Nothing restarts\. bizypro is parked/);
  const wire = JSON.stringify(sw.body);
  for (const leak of ['aaaaaaaaaaaa', 'bbbbbbbbbbbb', '/home/x', 'inode']) assert.ok(!wire.includes(leak), leak);
  const no = switchAnswer('beta', cli(0, { status: 'noop', from: 'beta' }), { beta: 'strategysimple' });
  assert.deepEqual([no.http, no.body.ok, no.body.status], [200, true, 'noop']);
});

test('every refusal reason is a 409 (503 when busy) in words; the detail is not echoed', () => {
  const cases: Array<[string, RegExp, number]> = [
    ['account-live-at-home', /signed in in another Claude session right now \(pid 774272\)\. Close that session/, 409],
    ['account-on-another-seat', /One agent holds/, 409],
    ['account-is-the-fleet-login', /main login/, 409],
    ['account-not-enrolled', /No Claude account is called beta/, 409],
    ['shared-login-unnamed', /does not say which account the main login is/, 409],
    ['cli-lock-busy', /refreshing a login right now/, 503],
    ['registry-lock-busy', /registry is busy/, 503],
    ['pending-switch', /stopped half-way/, 409],
    ['vault-slot-occupied', /nothing was overwritten/, 409],
    ['symlink-refused', /is a link/, 409],
    ['not-a-regular-file', /not a plain file/, 409],
    ['login-missing', /not where the ledger says/, 409],
    ['config-json-unparsable', /settings file .* cannot be read/, 409],
    ['label-splice-mismatch', /label could not be moved/, 409],
    ['login-empty', /signed out .* fresh \/login/, 409],
    ['outgoing-login-blank', /all agents use now was signed out by Claude .* Sign in again before switching/, 409],
    ['something-new', /Not switched \(something-new\)/, 409],
  ];
  for (const [reason, re, http] of cases) {
    const a = switchAnswer('beta', cli(2, { status: 'refused', reason, detail: '/home/shaw/.claude-beta used by pid 774272' }));
    assert.equal(a.http, http, reason);
    assert.equal(a.body.ok, false, reason);
    assert.equal(a.body.reason, reason);
    assert.match(a.body.message, re, reason);
    assert.ok(!a.body.message.includes('/home/shaw'), `${reason}: no path`);
  }
});

test('a timeout says it may have happened (never "nothing changed"); no answer or no CLI is a 502', () => {
  const t = switchAnswer('beta', { code: null, stdout: '', timedOut: true });
  assert.deepEqual([t.http, t.body.ok, t.body.reason], [504, false, 'timeout']);
  assert.match(t.body.message, /may still have happened/);
  assert.equal(switchAnswer('beta', { code: 1, stdout: 'Traceback\n', timedOut: false }).http, 502);
  const nr = switchAnswer('beta', { code: null, stdout: '', timedOut: false, error: 'ENOENT' });
  assert.deepEqual([nr.http, nr.body.reason], [502, 'cli-not-run']);
  assert.equal(parseCliLine('noise\n{"status":"noop"}\n\n').status, 'noop');
});

test('the deadline answers but never kills the switch (a login move is never cut off half-way)', async () => {
  const h = new Host();
  const mark = join(h.root, 'finished');
  const r = await runCli('sh', ['-c', `sleep 1; echo done > ${mark}; echo '{"status":"noop"}'`], process.env, 200);
  assert.equal(r.timedOut, true);
  const end = Date.now() + 5000;
  while (!existsSync(mark) && Date.now() < end) await new Promise((res) => setTimeout(res, 100));
  assert.ok(existsSync(mark), 'the child ran to its end');
});

// ---- end to end: the real switch on scratch dirs ---------------------------------------------------------

test('E2E: all agents move to beta, the displaced login is parked, every login exists exactly once, 0600 regular', { skip: !cliHere && 'switch CLI not found' }, async () => {
  const h = new Host();
  try {
    writeLogin(h.fleet, { ...login('default'), mcpOAuth: { srv: { accessToken: 'MCP-SENTINEL' } } });
    writeLogin(h.beta, login('beta'));
    // Claude's settings files, with a fake account label each (the switch moves the label with the login)
    writeFileSync(join(h.home, '.claude.json'), JSON.stringify({ numStartups: 7, oauthAccount: { emailAddress: 'default@example.invalid' } }));
    writeFileSync(join(h.beta, '.claude.json'), JSON.stringify({ oauthAccount: { emailAddress: 'beta@example.invalid' } }));
    const ino = lstatSync(join(h.fleet, '.credentials.json')).ino;
    const a = await h.switchTo('beta');
    assert.equal(a.body.status, 'switched', a.body.message);
    assert.equal(whoAt(h.fleet), 'beta');
    const st = lstatSync(join(h.fleet, '.credentials.json'));
    assert.ok(st.isFile() && !st.isSymbolicLink());
    assert.equal(st.mode & 0o777, 0o600);
    assert.notEqual(st.ino, ino, 'a new inode: the CLI sees the change on its next call');
    const vault = join(h.od, 'state', 'account-vault', 'default', '.credentials.json');
    assert.deepEqual(h.copies(), { default: [vault], beta: [join(h.fleet, '.credentials.json')] }, 'MOVE, never copy');
    assert.equal(JSON.parse(readFileSync(join(h.fleet, '.credentials.json'), 'utf8')).mcpOAuth.srv.accessToken, 'MCP-SENTINEL', 'MCP logins stay');
    assert.equal(JSON.parse(readFileSync(join(h.home, '.claude.json'), 'utf8')).numStartups, 7, 'the rest of Claude\'s settings stays');
    const v = (await loadAccountsView({ dataDir: h.od, home: h.home, run: h.run, cli: CLI }))!;
    assert.equal(v.shared, 'beta', 'the real --holders names the account all agents are on');
    assert.deepEqual(v.accounts.map((x) => [x.id, x.where, x.canUse]), [['default', 'vault', true], ['beta', 'shared', false]]);
    // and back: press the other card
    const back = await h.switchTo('default');
    assert.equal(back.body.status, 'switched', back.body.message);
    assert.equal(whoAt(h.fleet), 'default');
    const c = h.copies();
    assert.equal(c.default.length, 1);
    assert.equal(c.beta.length, 1, 'beta parked once, nowhere else');
  } finally { h.done(); }
});

test('E2E: a refresh that lands just before the press is what gets parked (the read is at switch time)', { skip: !cliHere && 'switch CLI not found' }, async () => {
  const h = new Host();
  try {
    writeLogin(h.fleet, login('default', 'RT-defaultold-SENTINEL'));
    writeLogin(h.beta, login('beta'));
    writeLogin(h.fleet, login('default', 'RT-defaultnew-SENTINEL'));   // Claude refreshed the fleet login: new refresh token
    const a = await h.switchTo('beta');
    assert.equal(a.body.status, 'switched', a.body.message);
    const c = h.copies();
    assert.deepEqual(c.defaultnew, [join(h.od, 'state', 'account-vault', 'default', '.credentials.json')]);
    assert.equal(c.defaultold, undefined, 'the stale pre-refresh login is nowhere');
  } finally { h.done(); }
});

test('E2E: refused while the target login is live in another session; nothing changes', { skip: !cliHere && 'switch CLI not found' }, async () => {
  const h = new Host();
  try {
    writeLogin(h.fleet, login('default'));
    writeLogin(h.beta, login('beta'));
    const pid = h.claudeOn(h.beta);
    await new Promise((res) => setTimeout(res, 300));
    const card = (await loadAccountsView({ dataDir: h.od, home: h.home, run: h.run, cli: CLI }))!.accounts[1];
    assert.equal(card.canUse, false, 'the card is disabled before any press');
    assert.match(card.whyNot!, new RegExp(`pid ${pid}`));
    const before = h.snapshot();
    const a = await h.switchTo('beta');
    assert.equal(a.http, 409);
    assert.equal(a.body.reason, 'account-live-at-home');
    assert.match(a.body.message, new RegExp(`pid ${pid}`));
    assert.deepEqual(h.snapshot(), before, 'nothing changed');
  } finally { h.done(); }
});

test('E2E: a symlinked login is refused, never followed; nothing changes', { skip: !cliHere && 'switch CLI not found' }, async () => {
  const h = new Host();
  try {
    writeLogin(h.fleet, login('default'));
    writeLogin(join(h.root, 'elsewhere'), login('beta'));
    symlinkSync(join(h.root, 'elsewhere', '.credentials.json'), join(h.beta, '.credentials.json'));
    const before = h.snapshot();
    const a = await h.switchTo('beta');
    assert.equal(a.body.reason, 'symlink-refused');
    assert.deepEqual(h.snapshot(), before);
  } finally { h.done(); }
});

test('E2E: a signed-out login in use (Claude blanked it after a failed refresh) is never parked as a login', { skip: !cliHere && 'switch CLI not found' }, async () => {
  const h = new Host();
  try {
    writeLogin(h.fleet, { claudeAiOauth: { accessToken: '', refreshToken: '', expiresAt: 0 } });
    writeLogin(h.beta, login('beta'));
    const before = h.snapshot();
    const a = await h.switchTo('beta');
    assert.equal(a.body.ok, false);
    assert.equal(a.body.reason, 'sign-in-needed');
    assert.match(a.body.message, /signed out by Claude .* Sign in again before switching/);
    assert.deepEqual(h.snapshot(), before);
    assert.ok(!existsSync(join(h.od, 'state', 'account-vault', 'default', '.credentials.json')));
  } finally { h.done(); }
});

test('E2E: while Claude holds its refresh lock on ~/.claude the switch waits, then refuses; nothing changes', { skip: !cliHere && 'switch CLI not found', timeout: 60_000 }, async () => {
  const h = new Host();
  try {
    writeLogin(h.fleet, login('default'));
    writeLogin(h.beta, login('beta'));
    mkdirSync(join(h.fleet, '.oauth_refresh.lock'));            // a live refresh (fresh lock dir, not stale)
    const before = h.snapshot();
    const a = await h.switchTo('beta');
    assert.equal(a.http, 503);
    assert.equal(a.body.reason, 'cli-lock-busy');
    assert.deepEqual(h.snapshot(), before);
    assert.equal(whoAt(h.fleet), 'default');
  } finally { h.done(); }
});

// Every refusal code the switch core can raise (core 7a5b68205). Always checked, so this file never passes because the
// core is absent; when the core is here its codes are read straight from it too.
const CORE_REFUSALS_7A5B68205 = ['account-already-held', 'account-is-the-fleet-login', 'account-live-at-home', 'account-login-dead',
  'account-not-enrolled', 'account-on-another-seat', 'account-on-the-shared-dir', 'account-still-in-vault', 'bad-label', 'cli-lock-busy',
  'config-json-missing', 'config-json-unparsable', 'label-splice-mismatch', 'login-empty', 'login-missing', 'login-unreadable',
  'mixed-config-dirs', 'no-canonical-seat', 'no-claude-process', 'no-live-pane', 'no-source-label', 'not-a-regular-file',
  'outgoing-login-blank', 'pending-switch', 'registry-lock-busy', 'registry-unreadable', 'seat-login-unnamed', 'shared-login-unnamed',
  'sign-in-needed', 'symlink-refused', 'vault-slot-occupied'];

test('every refusal the core can give has plain words (fixed list of core 7a5b68205)', () => {
  const words = refusalWords('beta', 'beta');
  const missing = CORE_REFUSALS_7A5B68205.filter((r) => !words[r]);
  assert.deepEqual(missing, []);
  for (const r of CORE_REFUSALS_7A5B68205) assert.doesNotMatch(words[r], /\/home\/|\.credentials|fp[ _]/, r);
});

test('every refusal code in the installed core has plain words', { skip: !cliHere && 'switch CLI not found' }, () => {
  const codes = [...new Set([...readFileSync(CLI, 'utf8').matchAll(/SwitchRefused\(\s*["']([a-z][a-z-]*)["']/g)].map((m) => m[1]))];
  assert.ok(codes.length >= CORE_REFUSALS_7A5B68205.length, `read ${codes.length} codes from the core`);
  const words = refusalWords('beta', 'beta');
  assert.deepEqual(codes.filter((r) => !words[r]), [], 'add plain words to refusalWords() for these core codes');
});

test('the switched message says where the replaced login went; an unknown value is never echoed', () => {
  const said = (where: unknown) => switchAnswer('strategysimple',
    cli(0, { status: 'switched', from: 'bizypro', followers: 3, ...(where === undefined ? {} : { displaced_to: where }) })).body.message;
  assert.match(said('home'), /bizypro's login went back to its own folder/);
  assert.match(said('vault'), /bizypro is parked/);
  assert.match(said(undefined), /bizypro is parked/);
  assert.match(said('nothing (signed out by the CLI)'), /already been signed out by Claude, so nothing was parked/);
  const odd = said('/home/x/.claude-weird SECRETPATH');
  assert.doesNotMatch(odd, /SECRETPATH|\/home\//);
  assert.match(odd, /card shows where its login is now/);
});

test('sign-in-needed and account-login-dead refuse in plain words, never the raw code', () => {
  for (const reason of ['sign-in-needed', 'account-login-dead']) {
    const a = switchAnswer('beta', cli(1, { status: 'refused', reason, detail: '/home/shaw/.claude: signed out' }));
    assert.equal(a.http, 409);
    assert.equal(a.body.ok, false);
    assert.doesNotMatch(a.body.message, new RegExp(`\\(${reason}\\)|/home/`));
    assert.match(a.body.message, /signed out by Claude/);
  }
});

test('an unknown refusal is never echoed raw, and a built-in object name is not a table entry', () => {
  for (const reason of ['constructor', '__proto__', 'toString', '/home/x SECRET', 'brand-new-code']) {
    const a = switchAnswer('beta', cli(1, { status: 'refused', reason }));
    assert.equal(a.body.ok, false);
    assert.equal(typeof a.body.message, 'string');
    assert.match(a.body.message, /Nothing was changed/);
    assert.doesNotMatch(a.body.message, /SECRET|\/home\/|function|\[object/);
  }
  assert.match(switchAnswer('beta', cli(1, { status: 'refused', reason: 'brand-new-code' })).body.message, /\(brand-new-code\)/);
  assert.doesNotMatch(refusalWords('beta', 'beta')['pending-switch'], /state\/|\.json/);
});

test('a login Claude signed out (--holders where "dead") reads "Signed out" and cannot be pressed', () => {
  const h = new Host();
  const v = accountsView({ dataDir: h.od, home: h.home, holders: H({
    default: { where: 'dead', path: null, live_pids: [] }, beta: { where: 'shared', live_pids: [1, 2] } }) })!;
  const card = v.accounts.find((a) => a.id === 'default')!;
  assert.equal(card.canUse, false);
  assert.match(card.whyNot!, /^Signed out: /);
  assert.doesNotMatch(card.whyNot!, /another Claude session|does not know/);
});

// ---- Codex: --provider codex ---------------------------------------------------------------------------------

const CH = (over: Record<string, object> = {}) => ({
  shawcole: { where: 'shared', email: 'shaw.cole@bizypro.co', name: 'shaw.cole', agents: 4 },
  shaw: { where: 'vault', email: 'shaw@bizypro.co', name: 'shaw', agents: 0 },
  ...over,
});

test('Codex cards from --provider codex --holders: current, followers, names/e-mails, canUse/whyNot', async () => {
  const h = new Host();
  const v = codexAccountsView({ dataDir: h.od, holders: CH() })!;
  assert.equal(v.provider, 'codex');
  assert.equal(v.shared, 'shawcole');
  assert.equal(v.followers, 4);
  assert.deepEqual(v.seats, {});
  assert.equal(v.terms_text, CODEX_TERMS_LINE);
  assert.equal(v.terms_ack, false);
  assert.deepEqual(v.current, { account: 'shawcole', name: 'shaw.cole', email: 'shaw.cole@bizypro.co', since: null, since_text: null, by: null });
  assert.deepEqual(v.accounts, [
    { id: 'shawcole', name: 'shaw.cole', email: 'shaw.cole@bizypro.co', where: 'shared', agents: 4, canUse: false, whyNot: 'This is the login all Codex agents use now.', note: null },
    { id: 'shaw', name: 'shaw', email: 'shaw@bizypro.co', where: 'vault', agents: 0, canUse: true, whyNot: null, note: null },
  ]);
  const miss = codexAccountsView({ dataDir: h.od, holders: CH({ shaw: { where: 'missing', email: null, name: 'shaw', agents: 0 } }) })!;
  assert.deepEqual([miss.accounts[1].where, miss.accounts[1].canUse, miss.accounts[1].whyNot],
    ['missing', false, 'This login is not in its vault slot; nothing to switch to.']);
  assert.equal(codexAccountsView({ dataDir: h.od, holders: { shawcole: CH().shawcole } }), null, 'one Codex account: no cards');
  // unreadable holders: the registry's Codex profiles, all disabled (the Host registry has one: fewer than two -> null)
  assert.equal(codexAccountsView({ dataDir: h.od, holders: null }), null);
  writeFileSync(join(h.od, 'state', 'claude_profiles.json'), JSON.stringify({ profiles: [
    { id: 'shawcole', provider: 'codex', config_dir: '~/.codex', enrolled: true }, { id: 'shaw', provider: 'codex', config_dir: '~/.codex-shaw', enrolled: true }] }));
  const none = codexAccountsView({ dataDir: h.od, holders: null })!;
  assert.deepEqual(none.accounts.map((a) => [a.canUse, a.whyNot]), [[false, 'Where each login is cannot be read right now.'], [false, 'Where each login is cannot be read right now.']]);
  assert.equal(none.shared, null);
  let args: string[] = [];
  const v2 = await loadCodexAccountsView({ dataDir: h.od, cli: '/x/s.py', run: async (_p, a) => { args = a; return { code: 0, stdout: JSON.stringify(CH()), timedOut: false }; } });
  assert.deepEqual(args, ['/x/s.py', '--provider', 'codex', '--holders']);
  assert.equal(v2!.shared, 'shawcole');
});

test('Codex top line: since/by from Codex ledger rows only; Claude ignores Codex rows', () => {
  const h = new Host();
  mkdirSync(join(h.od, 'logs'), { recursive: true });
  writeFileSync(join(h.od, 'logs', 'account-switch.jsonl'), [
    { ts: '2026-11-21T20:23:13+00:00', seat: '*shared*', to: 'beta', status: 'switched', from: 'default' },
    { ts: '2026-11-21T20:30:00+00:00', provider: 'codex', kind: 'shared', from: 'shawcole', to: 'shaw', status: 'switched', by: null, displaced_to: 'vault', followers: 2 },
    { ts: '2026-11-21T20:40:00+00:00', provider: 'codex', kind: 'shared', to: 'beta', status: 'switched', from: 'shaw' },
  ].map((r) => JSON.stringify(r)).join('\n') + '\n');
  writeFileSync(join(h.od, 'state', 'shaw-location.json'), JSON.stringify({ tz: 'America/New_York', label: 'ET' }));
  const now = new Date('2026-11-21T21:00:00Z');
  recordSwitchBy(h.od, 'shaw', 'shaw (dashboard)', new Date('2026-11-21T20:30:01Z'), 'codex');
  const v = codexAccountsView({ dataDir: h.od, now, holders: CH({ shawcole: { where: 'vault', name: 'shaw.cole', agents: 0 }, shaw: { where: 'shared', name: 'shaw', agents: 2 } }) })!;
  assert.deepEqual([v.current!.since, v.current!.since_text, v.current!.by], ['2026-11-21T20:30:00+00:00', '3:30 PM ET', 'shaw (dashboard)']);
  const c = accountsView({ dataDir: h.od, home: h.home, now, holders: H({ default: { where: 'vault', live_pids: [] }, beta: { where: 'shared', live_pids: [1] } }) })!;
  assert.equal(c.current!.since, '2026-11-21T20:23:13+00:00', 'a Codex row is never a Claude switch');
  assert.equal(c.current!.by, 'hand (account_switch.py)', 'the Codex presser is not the Claude one');
});

test('terms are acknowledged per provider: Codex has its own, Claude\'s file and key stay as they were', () => {
  const h = new Host();
  recordTermsAck(h.od, 'dashboard');
  assert.equal(termsAcknowledged(h.od), true);
  assert.equal(termsAcknowledged(h.od, 'codex'), false, 'a Claude ack is not a Codex ack');
  recordTermsAck(h.od, 'dashboard', new Date(), 'codex');
  assert.equal(termsAcknowledged(h.od, 'codex'), true);
  assert.equal(JSON.parse(readFileSync(join(h.od, 'state', 'account_terms_ack_codex.json'), 'utf8')).text, CODEX_TERMS_LINE);
  assert.equal(JSON.parse(readFileSync(join(h.od, 'state', 'account_terms_ack.json'), 'utf8')).text, TERMS_LINE);
  assert.equal(statSync(join(h.od, 'state', 'account_terms_ack_codex.json')).mode & 0o777, 0o600);
});

test('Codex switch: args, switched/noop words, refusals say Codex, unknown token is generic', async () => {
  let args: string[] = [];
  const run: RunCli = async (_p, a) => { args = a; return cli(0, { status: 'switched', from: 'shawcole', to: 'shaw', displaced_to: 'vault', followers: 4 }); };
  const sw = await switchAllAgents('shaw', { dataDir: '/nonexistent', cli: '/x/s.py', run, provider: 'codex', names: { shaw: 'shaw', shawcole: 'shaw.cole' } });
  assert.deepEqual(args, ['/x/s.py', '--provider', 'codex', '--shared', 'shaw']);
  assert.equal(sw.http, 200);
  assert.equal(sw.body.from, 'shawcole');
  assert.equal(sw.body.message, 'New Codex sessions and reviews use shaw from now on. Running Codex agents keep shaw.cole until their next start. '
    + 'shaw.cole is parked; press its card to switch back.');
  const no = switchAnswer('shaw', cli(0, { status: 'noop' }), {}, 'codex');
  assert.deepEqual([no.http, no.body.status, no.body.message], [200, 'noop', 'shaw is already the login all Codex agents use.']);
  // every reason scripts/codex_account_switch.py can raise (Claude review of #676)
  const tokens = ['account-not-enrolled', 'login-missing', 'login-unreadable', 'login-empty', 'outgoing-login-blank', 'shared-login-unnamed',
    'vault-slot-occupied', 'registry-lock-busy', 'symlink-refused', 'not-a-regular-file', 'pending-switch', 'io-error',
    'vault-slot-in-use', 'registry-unreadable'];
  for (const t of ['pending-switch', 'io-error']) {
    const m = codexRefusalWords('shaw', 'shaw')[t] || '';
    assert.doesNotMatch(m, /nothing was changed/i, t);          // it may have stopped half-way: never "nothing changed"
    assert.match(m, /account-switch\.jsonl/, t);
  }
  const words = codexRefusalWords('shaw', 'shaw');
  assert.deepEqual(tokens.filter((t) => !words[t]), []);
  for (const t of tokens) {
    const a = switchAnswer('shaw', cli(2, { status: 'refused', reason: t, detail: '/home/shaw/.codex/auth.json' }), {}, 'codex');
    assert.equal(a.http, t === 'registry-lock-busy' ? 503 : 409, t);
    assert.match(a.body.message, /Codex/, t);
    assert.doesNotMatch(a.body.message, /Claude|\/home\//, t);
  }
  assert.match(switchAnswer('shaw', cli(2, { status: 'refused', reason: 'brand-new-code' }), {}, 'codex').body.message, /^Not switched \(brand-new-code\)\. Nothing was changed\.$/);
});

test('Codex usage note: a window at 90% or more says so with its reset in Shaw\'s zone; under 90% or no file is null; canUse unchanged', () => {
  const h = new Host();
  mkdirSync(join(h.od, 'state', 'usage'), { recursive: true });
  writeFileSync(join(h.od, 'state', 'shaw-location.json'), JSON.stringify({ tz: 'America/Cancun', label: 'Tulum' }));
  const now = new Date('2026-11-26T20:00:00Z');
  const reset = Date.parse('2026-11-27T03:49:00Z') / 1000;               // 10:49 PM Tulum, same day
  const usage = (id: string, w: object) => writeFileSync(join(h.od, 'state', 'usage', `api-codex-${id}.json`), JSON.stringify({ windows: w, as_of: 1 }));
  usage('shaw', { '5h': { used_pct: 92.6, resets_at: reset }, weekly: { used_pct: 40, resets_at: reset } });
  usage('shawcole', { '5h': { used_pct: 50, resets_at: reset }, weekly: { used_pct: 50, resets_at: reset } });
  const v = codexAccountsView({ dataDir: h.od, holders: CH(), now })!;
  const shaw = v.accounts.find((a) => a.id === 'shaw')!;
  assert.equal(shaw.note, '5-hour at 93%, resets 10:49 PM Tulum');
  assert.equal(shaw.canUse, true, 'a note never disables the card');
  assert.equal(v.accounts.find((a) => a.id === 'shawcole')!.note, null, '50%: no note');
  usage('shaw', { '5h': { used_pct: 95, resets_at: reset }, weekly: { used_pct: 90, resets_at: reset + 86_400 } });
  assert.equal(codexUsageNote(h.od, 'shaw', now), '5-hour at 95%, resets 10:49 PM Tulum; weekly at 90%, resets Fri 10:49 PM Tulum');
  assert.equal(codexUsageNote(h.od, 'nobody', now), null, 'no file');
  writeFileSync(join(h.od, 'state', 'usage', 'api-codex-shaw.json'), '{not json');
  assert.equal(codexUsageNote(h.od, 'shaw', now), null, 'unreadable');
});
