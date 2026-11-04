import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SETTINGS, settingEntry, type GatewayClient, type SettingsCtx } from './settingsRegistry.js';

const TOML = '[operator]\nid = "operator"\n# Your timezone, as an IANA name\ntimezone = "America/New_York"\n\n[rotation]\nctx_ceiling_pct = 0.80\n';

function fakeGateway() {
  const calls: { method: string; path: string; body?: any }[] = [];
  let vendor = 'openai'; let voice = 'marin';
  const gateway: GatewayClient = {
    get: async (p) => {
      calls.push({ method: 'GET', path: p });
      if (p === '/arturo/ptt/vendor') return { status: 200, body: { ok: true, vendor, vendors: [
        { id: 'openai', title: 'GPT-Live', available: true }, { id: 'hume', title: 'Hume', available: false, reason: 'no key' }] } };
      if (p.startsWith('/arturo/ptt/voice?')) return { status: 200, body: { ok: true, voice_id: voice, title: voice } };
      return { status: 404, body: { ok: false, error: 'nope' } };
    },
    put: async (p, body: any) => {
      calls.push({ method: 'PUT', path: p, body });
      if (p === '/arturo/ptt/vendor') {
        if (body.vendor === 'hume') return { status: 400, body: { ok: false, error: 'hume is not set up' } };
        vendor = body.vendor; return { status: 200, body: { ok: true } };
      }
      if (p === '/arturo/ptt/voice') { voice = body.voice_id; return { status: 200, body: { ok: true } }; }
      return { status: 404, body: { ok: false } };
    },
  };
  return { gateway, calls };
}

function makeCtx() {
  const dataDir = mkdtempSync(join(tmpdir(), 'settings-reg-'));
  mkdirSync(join(dataDir, 'state'));
  const configPath = join(dataDir, 'orchestra.toml');
  writeFileSync(configPath, TOML);
  const { gateway, calls } = fakeGateway();
  const ctx: SettingsCtx = { dataDir, configPath, user: 'operator', gateway };
  return { ctx, calls };
}

test('every key is unique, in a phase-1 section, names its store; every Everyone write is admin-only', () => {
  const keys = SETTINGS.map((e) => e.key);
  assert.equal(new Set(keys).size, keys.length);
  for (const e of SETTINGS) {
    assert.ok(['general', 'voice', 'agents'].includes(e.section), e.key);
    assert.ok(e.store.length > 0, `${e.key} names its store`);
    if (e.readOnly) continue;
    assert.equal(typeof e.write, 'function', `${e.key} has a writer`);
    if (e.scope === 'everyone') assert.equal(e.admin, true, `${e.key} is admin-only`);
  }
});

test('with nothing stored, each row reads its built-in default', async () => {
  const { ctx } = makeCtx();
  const v = async (k: string) => (await settingEntry(k)!.read(ctx));
  assert.deepEqual(await v('voice.dailyLimitMin'), { value: 120, source: 'builtin', detail: "From the voice service's environment until set here" });
  assert.deepEqual(await v('voice.newAgentsTakeCalls'), { value: true, source: 'builtin' });
  assert.deepEqual(await v('agents.compactAt'), { value: 65, source: 'builtin' });
  assert.deepEqual(await v('general.defaultChatStyle'), { value: 'bubbles', source: 'builtin' });
  assert.equal((await v('general.timeZone')).value, 'America/New_York');
});

const CASES: [string, unknown, unknown, string][] = [
  ['general.defaultChatStyle', 'modern', 'dev', 'state/users/operator/prefs.json'],
  ['general.timeZone', 'Europe/Berlin', 'Mars/Base', 'orchestra.toml'],
  ['voice.newAgentsTakeCalls', false, 'no', 'state/agent-calls.json'],
  ['voice.dailyLimitMin', 45, 0, 'state/voice-limits.json'],
  ['agents.autoCompact', true, 1, 'state/seat-settings-default.json'],
  ['agents.compactAt', 50, 90, 'state/seat-settings-default.json'],
  ['agents.showCompaction', false, 'x', 'state/seat-settings-default.json'],
];

test('local stores: validators reject bad input; good values round-trip into the store the entry names', async () => {
  const { ctx } = makeCtx();
  for (const [key, good, bad, file] of CASES) {
    const e = settingEntry(key)!;
    assert.equal(e.validate(good), null, `${key} accepts ${String(good)}`);
    assert.ok(e.validate(bad), `${key} rejects ${String(bad)}`);
    await e.write!(ctx, good);
    const r = await e.read(ctx);
    assert.deepEqual([r.value, r.source], [good, 'store'], key);
    assert.ok(existsSync(join(ctx.dataDir, file)), `${key} wrote ${file}`);
  }
  assert.match(readFileSync(ctx.configPath, 'utf-8'), /# Your timezone, as an IANA name\ntimezone = "Europe\/Berlin"/);
});

test('default model: one per runtime, the rest built in', async () => {
  const { ctx } = makeCtx();
  const e = settingEntry('agents.defaultModel')!;
  assert.ok(e.validate({ runtime: 'cobol', model: 'x' }));
  assert.ok(e.validate('claude'));
  assert.equal(e.validate({ runtime: 'codex', model: 'gpt-5.6-mini' }), null);
  await e.write!(ctx, { runtime: 'codex', model: 'gpt-5.6-mini' });
  const v = (await e.read(ctx)).value as Record<string, string>;
  assert.equal(v.codex, 'gpt-5.6-mini');
  assert.equal(v.claude, 'claude-opus-5-5[1m]');
});

test('voice provider and default voice go through the gateway; a refusal comes back as its reason', async () => {
  const { ctx, calls } = makeCtx();
  const p = settingEntry('voice.provider')!;
  const r = await p.read(ctx);
  assert.equal(r.value, 'openai');
  assert.deepEqual(r.choices, [
    { value: 'openai', label: 'GPT-Live', available: true },
    { value: 'hume', label: 'Hume', available: false, reason: 'Not available on this install' }]);
  await assert.rejects(p.write!(ctx, 'hume'), /hume is not set up/);
  await p.write!(ctx, 'openai');
  assert.deepEqual(calls.at(-1), { method: 'PUT', path: '/arturo/ptt/vendor', body: { vendor: 'openai', by: 'operator' } });

  const v = settingEntry('voice.defaultVoice')!;
  writeFileSync(join(ctx.dataDir, 'state', 'voice-choice.json'), JSON.stringify({ openai: { voice_id: 'marin' }, agents: { a: { openai: { voice_id: 'x' } }, b: { hume: {} } } }));
  assert.deepEqual(await v.read(ctx), { value: 'marin', source: 'store', detail: '1 agent uses its own voice' });
  await v.write!(ctx, 'cedar');
  assert.deepEqual(calls.at(-1), { method: 'PUT', path: '/arturo/ptt/voice', body: { vendor: 'openai', voice_id: 'cedar' } });
  assert.equal((await v.read(ctx)).value, 'cedar');
});

test('safety limits are read-only: forced compaction at 90%, the context ceiling from [rotation]', async () => {
  const { ctx } = makeCtx();
  const e = settingEntry('agents.safetyLimits')!;
  assert.equal(e.readOnly, true);
  assert.equal(e.write, undefined);
  assert.deepEqual((await e.read(ctx)).value, { forced_compaction_pct: 90, context_ceiling_pct: 80 });
});

test('a time zone the file cannot safely take is refused with the reason, and orchestra.toml is left untouched', async () => {
  const { ctx } = makeCtx();
  const odd = 'operator = { id = "operator", timezone = "UTC" }\n# hand-made\n';
  writeFileSync(ctx.configPath, odd);
  await assert.rejects(settingEntry('general.timeZone')!.write!(ctx, 'Europe/Paris'),
    { message: "Couldn't edit orchestra.toml safely; set [operator] timezone by hand" });
  assert.equal(readFileSync(ctx.configPath, 'utf-8'), odd);
});

test('the daily limit says where it comes from only while nothing is set here', async () => {
  const { ctx } = makeCtx();
  const e = settingEntry('voice.dailyLimitMin')!;
  assert.equal((await e.read(ctx)).detail, "From the voice service's environment until set here");
  await e.write!(ctx, 45);
  assert.deepEqual(await e.read(ctx), { value: 45, source: 'store' });
});

test('"New agents take calls" says it only covers Claude agents for now (headless_adopt adopts Claude seats only)', () => {
  assert.match(settingEntry('voice.newAgentsTakeCalls')!.note ?? '', /Claude agents only for now/);
});

test('the provider change names the person who made it (their Tailscale login), not the operator account', async () => {
  const { ctx, calls } = makeCtx();
  await settingEntry('voice.provider')!.write!({ ...ctx, by: 'sam@example.com' }, 'openai');
  assert.deepEqual(calls.at(-1), { method: 'PUT', path: '/arturo/ptt/vendor', body: { vendor: 'openai', by: 'sam@example.com' } });
});
