/**
 * RED-first for the LIVE model catalog (issue: the picker must show every
 * model the operator's LOGIN can use, not a hand-written list that drifts).
 *
 * Every probe kind is exercised against CAPTURED REAL CLI OUTPUT (claude
 * 2.1.284 control protocol, codex 0.153.4 app-server, agy 1.2.13) through an
 * injected exec — the tests never spawn a CLI.
 *
 * Run: npx tsx --test src/routes/model-catalog.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  probeModelCatalog,
  type ProbeExec,
  type ModelProbeConfig,
} from './model-catalog.js';
import type { StaticModel } from './runtimes-available.js';

// ---- captured real output --------------------------------------------

const AGY_STDOUT = [
  'Fetching available models...',
  'gemini-3.8-flash-high\tGemini 3.8 Flash (High)',
  'gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)',
  'gemini-3.7-flash-high\tGemini 3.7 Flash (High)',
  '',
].join('\n');

const CLAUDE_STDOUT = [
  JSON.stringify({ type: 'system', subtype: 'hook_started', hook_name: 'SessionStart:startup' }),
  JSON.stringify({
    type: 'control_response',
    response: {
      response: {
        models: [
          { value: 'default', resolvedModel: 'claude-opus-5-5', displayName: 'Default (recommended)' },
          { value: 'opus', resolvedModel: 'claude-opus-5-5', displayName: 'Opus 5.5' },
          { value: 'claude-fable-5-1', resolvedModel: 'claude-fable-5-1', displayName: 'Fable 5.1' },
        ],
      },
    },
  }),
  '',
].join('\n');

const codexPage = (id: number, models: unknown[], nextCursor: string | null) =>
  JSON.stringify({ jsonrpc: '2.0', id, result: { data: models, nextCursor } });

const CODEX_STDOUT_ONE_PAGE = [
  JSON.stringify({ jsonrpc: '2.0', id: 1, result: { userAgent: 'codex' } }),
  codexPage(2, [
    { id: 'gpt-6-astra', displayName: 'GPT-6-Astra', hidden: false, inputModalities: ['text', 'image'] },
    { id: 'gpt-5.6-sol', displayName: 'GPT-5.6-Sol', hidden: false, inputModalities: ['text', 'image'] },
    { id: 'gpt-internal', displayName: 'Internal', hidden: true, inputModalities: ['text'] },
  ], null),
  '',
].join('\n');

// ---- configs mirroring what providers.json will carry ------------------

const AGY_PROBE: ModelProbeConfig = {
  kind: 'tsv-stdout', cmd: 'agy models', id_field: 0, label_field: 1,
};
const CLAUDE_PROBE: ModelProbeConfig = {
  kind: 'stdin-json-stream',
  resolved_key: 'resolvedModel',
  default_id: 'default',
  cmd: 'claude -p --input-format stream-json --output-format stream-json --verbose --no-session-persistence',
  stdin: '{"type":"control_request","request_id":"probe","request":{"subtype":"initialize"}}',
  match: { type: 'control_response' },
  list_path: 'response.response.models',
  id_key: 'value',
  label_key: 'displayName',
};
const CODEX_PROBE: ModelProbeConfig = {
  kind: 'jsonrpc-stdio',
  cmd: 'codex app-server',
  method: 'model/list',
  list_path: 'result.data',
  cursor_path: 'result.nextCursor',
  id_key: 'id',
  label_key: 'displayName',
  hidden_key: 'hidden',
  modalities_key: 'inputModalities',
};

function execReturning(
  stdout: string,
  spy?: { calls: { bin: string; args: string[]; input?: string; lingerMs?: number }[] },
): ProbeExec {
  return (bin, args, opts) => {
    spy?.calls.push({ bin, args, input: opts.input, lingerMs: opts.lingerMs });
    return stdout;
  };
}

const STATIC: StaticModel[] = [
  {
    id: 'claude-fable-5-1',
    label: 'Fable 5.1 (static)',
    capabilities: { text: true, image: true, audio: false, video: false, context_window: 400000 },
  },
];

// ---- tsv-stdout --------------------------------------------------------

test('tsv-stdout: parses id/label and SKIPS the CLI preamble line', () => {
  const got = probeModelCatalog(AGY_PROBE, [], execReturning(AGY_STDOUT));
  assert.equal(got.source, 'probe');
  assert.deepEqual(got.models.map((m) => m.id), [
    'gemini-3.8-flash-high', 'gemini-3.8-flash-medium', 'gemini-3.7-flash-high',
  ]);
  assert.equal(got.models[0].label, 'Gemini 3.8 Flash (High)');
});

// ---- stdin-json-stream -------------------------------------------------

test('stdin-json-stream: writes the control request and reads the matching line only', () => {
  const spy = { calls: [] as { bin: string; args: string[]; input?: string; lingerMs?: number }[] };
  const got = probeModelCatalog(CLAUDE_PROBE, [], execReturning(CLAUDE_STDOUT, spy));
  assert.equal(got.source, 'probe');
  // 'default' is the CLI's own pointer row, not a model — see the alias tests below
  assert.deepEqual(got.models.map((m) => m.id), ['opus', 'claude-fable-5-1']);
  assert.equal(spy.calls[0].bin, 'claude');
  assert.match(spy.calls[0].input || '', /control_request/);
});

test('stdin-json-stream: a model the probe finds keeps VERIFIED static capabilities', () => {
  const got = probeModelCatalog(CLAUDE_PROBE, STATIC, execReturning(CLAUDE_STDOUT));
  const fable = got.models.find((m) => m.id === 'claude-fable-5-1');
  assert.ok(fable);
  assert.equal(fable.capabilities.context_window, 400000);
  // the LIVE label wins over the static one; the login is the source of truth
  assert.equal(fable.label, 'Fable 5.1');
  const unknown = got.models.find((m) => m.id === 'opus');
  assert.ok(unknown);
  assert.ok((unknown.capabilities_unverified || []).includes('context_window'));
});

// ---- jsonrpc-stdio -----------------------------------------------------

test('jsonrpc-stdio: sends handshake, drops hidden models, reads modalities', () => {
  const spy = { calls: [] as { bin: string; args: string[]; input?: string; lingerMs?: number }[] };
  const got = probeModelCatalog(CODEX_PROBE, [], execReturning(CODEX_STDOUT_ONE_PAGE, spy));
  assert.equal(got.source, 'probe');
  assert.deepEqual(got.models.map((m) => m.id), ['gpt-6-astra', 'gpt-5.6-sol']);
  assert.equal(got.models[0].capabilities.image, true);
  const input = spy.calls[0].input || '';
  assert.match(input, /"method":"initialize"/);
  assert.match(input, /"method":"initialized"/);
  assert.match(input, /"method":"model\/list"/);
  // codex app-server exits on EOF before answering: stdin must be held open
  assert.ok((spy.calls[0].lingerMs || 0) > 0, 'jsonrpc-stdio must linger on stdin');
});

test('stdin-json-stream does NOT linger — it answers and exits on its own', () => {
  const spy = { calls: [] as { bin: string; args: string[]; input?: string; lingerMs?: number }[] };
  probeModelCatalog(CLAUDE_PROBE, [], execReturning(CLAUDE_STDOUT, spy));
  assert.equal(spy.calls[0].lingerMs, undefined);
});

test('jsonrpc-stdio: FOLLOWS nextCursor — a truncated list is a wrong list', () => {
  let call = 0;
  const pages = [
    [
      JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} }),
      codexPage(2, [{ id: 'page1', displayName: 'Page 1' }], 'CURSOR2'),
    ].join('\n'),
    [
      JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} }),
      codexPage(2, [{ id: 'page2', displayName: 'Page 2' }], null),
    ].join('\n'),
  ];
  const inputs: string[] = [];
  const exec: ProbeExec = (_bin, _args, opts) => {
    inputs.push(opts.input || '');
    return pages[Math.min(call++, pages.length - 1)];
  };
  const got = probeModelCatalog(CODEX_PROBE, [], exec);
  assert.deepEqual(got.models.map((m) => m.id), ['page1', 'page2']);
  assert.match(inputs[1], /CURSOR2/);
});

test('jsonrpc-stdio: a cursor that never ends is bounded, not an infinite loop', () => {
  const exec: ProbeExec = () => [
    JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} }),
    codexPage(2, [{ id: 'loop', displayName: 'Loop' }], 'ALWAYS'),
  ].join('\n');
  const got = probeModelCatalog(CODEX_PROBE, [], exec);
  assert.equal(got.source, 'probe');
  assert.ok(got.models.length <= 20, 'the page cap must bound the result');
  // bounded EITHER by the repeat-cursor guard or the hard cap — but it must say so
  assert.match(got.reason || '', /cursor-loop|page-cap/);
});

// ---- failure is LOUD, and never empties the picker ---------------------

test('a probe that throws falls back to static and SAYS why', () => {
  const boom: ProbeExec = () => { throw new Error('ENOENT'); };
  const got = probeModelCatalog(CLAUDE_PROBE, STATIC, boom);
  assert.equal(got.source, 'static-fallback');
  assert.deepEqual(got.models.map((m) => m.id), ['claude-fable-5-1']);
  assert.match(got.reason || '', /probe-failed/);
});

test('a probe that returns nothing usable falls back rather than reporting zero models', () => {
  const got = probeModelCatalog(AGY_PROBE, STATIC, execReturning('Fetching available models...\n'));
  assert.equal(got.source, 'static-fallback');
  assert.equal(got.models.length, 1);
  assert.match(got.reason || '', /probe-empty/);
});

test('an unknown probe kind is reported, not silently empty', () => {
  const got = probeModelCatalog(
    { kind: 'wat' as ModelProbeConfig['kind'], cmd: 'x' }, STATIC, execReturning(''));
  assert.equal(got.source, 'static-fallback');
  assert.match(got.reason || '', /unknown-probe-kind/);
});

test('duplicate ids collapse — one row per model', () => {
  const dupes = ['a\tA', 'a\tA again', 'b\tB'].join('\n');
  const got = probeModelCatalog(AGY_PROBE, [], execReturning(dupes));
  assert.deepEqual(got.models.map((m) => m.id), ['a', 'b']);
});


// --- aliases: the CLI lists POINTERS next to real models -------------------
// `claude` returns `default` (-> claude-opus-5-5) beside `opus` (-> the same model). The
// sheet already offers "Use the default brain" as the empty-model row, so the probe's
// `default` rendered as a SECOND default ("Default (recommended)") directly under it
// (operator, 2026-09-29). But `opus` is the ONLY id that reaches Opus 5.5, so dropping
// every alias would lose the model.

const CLAUDE_ALIASES = [
  { value: 'default', resolvedModel: 'claude-opus-5-5', displayName: 'Default (recommended)' },
  { value: 'opus', resolvedModel: 'claude-opus-5-5', displayName: 'Opus 5.5' },
  { value: 'sonnet', resolvedModel: 'claude-sonnet-5-5', displayName: 'Sonnet 5.5' },
  { value: 'claude-sonnet-5', resolvedModel: 'claude-sonnet-5', displayName: 'Sonnet 5' },
];

function claudeStdout(models: unknown[]): string {
  return JSON.stringify({ type: 'control_response', response: { response: { models } } });
}

test("the CLI's default POINTER is dropped — the sheet already has a default row", () => {
  const got = probeModelCatalog(CLAUDE_PROBE, [], execReturning(claudeStdout(CLAUDE_ALIASES)));
  assert.ok(!got.models.some((m) => m.id === 'default'), 'the default pointer must not be a model row');
});

test('an alias that is the ONLY way to reach a model is kept, under its own name', () => {
  const got = probeModelCatalog(CLAUDE_PROBE, [], execReturning(claudeStdout(CLAUDE_ALIASES)));
  const opus = got.models.find((m) => m.id === 'opus');
  assert.ok(opus, 'opus is the only id resolving to claude-opus-5-5');
  assert.equal(opus.label, 'Opus 5.5');
  // sonnet -> claude-sonnet-5-5 is NOT the same model as the concrete claude-sonnet-5 row
  assert.ok(got.models.some((m) => m.id === 'sonnet'));
  assert.ok(got.models.some((m) => m.id === 'claude-sonnet-5'));
});

test('when a CONCRETE id reaches the same model, the alias collapses into it', () => {
  const withConcrete = [
    ...CLAUDE_ALIASES,
    { value: 'claude-opus-5-5', resolvedModel: 'claude-opus-5-5', displayName: 'Opus 5.5' },
  ];
  const got = probeModelCatalog(CLAUDE_PROBE, [], execReturning(claudeStdout(withConcrete)));
  assert.ok(got.models.some((m) => m.id === 'claude-opus-5-5'));
  assert.ok(!got.models.some((m) => m.id === 'opus'), 'the alias duplicates a concrete row');
});

test('a provider with no alias concept is untouched', () => {
  const got = probeModelCatalog(AGY_PROBE, [], execReturning(AGY_STDOUT));
  assert.equal(got.models.length, 3);
});


// A collapsed alias is still a REAL --model argument. The picker stops offering it, but an
// operator whose saved pick predates the collapse must not start getting `unknown_model`.
test('valid_ids keeps what the picker no longer offers', () => {
  const got = probeModelCatalog(CLAUDE_PROBE, [], execReturning(claudeStdout(CLAUDE_ALIASES)));
  assert.ok(!got.models.some((m) => m.id === 'default'), 'not offered');
  assert.ok(got.valid_ids.includes('default'), 'still accepted');
  for (const m of got.models) assert.ok(got.valid_ids.includes(m.id), 'offered implies accepted');
});

test('valid_ids on a static fallback is just the static list', () => {
  const got = probeModelCatalog(CLAUDE_PROBE, STATIC, () => { throw new Error('ENOENT'); });
  assert.deepEqual(got.valid_ids, ['claude-fable-5-1']);
});

// ---- the login's DEFAULT model -----------------------------------------
// An empty model means "the CLI's default", and a brain told only "you are Codex" guesses its
// own model ("GPT-6"). The default is login-specific (staging: gpt-6.1-sol, host: gpt-6-astra),
// so it is read from the probe, never written down.

test('jsonrpc-stdio: the row flagged by default_key is the default model', () => {
  const stdout = [
    JSON.stringify({ jsonrpc: '2.0', id: 1, result: { userAgent: 'codex' } }),
    codexPage(2, [ // shape captured from codex app-server model/list, 2026-09-30
      { id: 'gpt-6.1-sol', displayName: 'GPT-6.1-Sol', hidden: false, isDefault: true, inputModalities: ['text'] },
      { id: 'gpt-5.6-luna', displayName: 'GPT-5.6-Luna', hidden: false, isDefault: false, inputModalities: ['text'] },
    ], null),
    '',
  ].join('\n');
  const r = probeModelCatalog({ ...CODEX_PROBE, default_key: 'isDefault' }, [], execReturning(stdout));
  assert.equal(r.default_model, 'gpt-6.1-sol');
});

test('stdin-json-stream: the default POINTER resolves to the default model', () => {
  const r = probeModelCatalog(CLAUDE_PROBE, [], execReturning(CLAUDE_STDOUT));
  assert.equal(r.default_model, 'claude-opus-5-5');
});

test('no default is claimed when the CLI does not say which it is', () => {
  const r = probeModelCatalog(AGY_PROBE, [], execReturning(AGY_STDOUT));
  assert.equal(r.default_model, undefined);
  const f = probeModelCatalog(CODEX_PROBE, [], () => { throw new Error('boom'); });
  assert.equal(f.default_model, undefined);
});

// ---- operator finding #8: labels must carry a version -----------------------------------
// CAPTURED from the pinned claude 2.1.276 (the INSTALL pin) on 2026-10-08: alias rows label
// "Opus (1M context)" / "Fable" / "Sonnet" / "Haiku" with the version only in the description
// head and the resolved id. The operator: "there's no version numbers, it feels hard coded".

const CLAUDE_2_1_276_STDOUT = [
  JSON.stringify({
    type: 'control_response',
    response: {
      response: {
        models: [
          { value: 'default', displayName: 'Default (recommended)', resolvedModel: 'claude-opus-5[1m]', description: 'Opus 5 with 1M context · Best for everyday, complex tasks' },
          { value: 'opus[1m]', displayName: 'Opus (1M context)', resolvedModel: 'claude-opus-5[1m]', description: 'Opus 5 with 1M context · Best for everyday, complex tasks' },
          { value: 'claude-fable-5-1[1m]', displayName: 'Fable', resolvedModel: 'claude-fable-5-1', description: 'Fable 5.1 · Most capable for your hardest and longest-running tasks' },
          { value: 'sonnet', displayName: 'Sonnet', resolvedModel: 'claude-sonnet-5', description: 'Sonnet 5 · Efficient for routine tasks' },
          { value: 'haiku', displayName: 'Haiku', resolvedModel: 'claude-haiku-4-5-20251001', description: 'Haiku 4.5 · Fastest for quick answers' },
          { value: 'opus', displayName: 'Opus', resolvedModel: 'claude-opus-5', description: 'Opus 5 · Best for everyday, complex tasks' },
        ],
      },
    },
  }),
  '',
].join('\n');

test('#8: a versionless alias label takes the version from the description head', () => {
  const r = probeModelCatalog({ ...CLAUDE_PROBE, description_key: 'description' }, [], execReturning(CLAUDE_2_1_276_STDOUT));
  const labels = Object.fromEntries(r.models.map((m) => [m.id, m.label]));
  assert.equal(labels['opus[1m]'], 'Opus 5 with 1M context');
  assert.equal(labels['claude-fable-5-1[1m]'], 'Fable 5.1');
  assert.equal(labels['sonnet'], 'Sonnet 5');
  assert.equal(labels['haiku'], 'Haiku 4.5');
  assert.equal(labels['opus'], 'Opus 5');
  for (const m of r.models) assert.match(m.label, /\d+(?:\.\d+)*(?![\dMKmk])/, `${m.id} still has no version: ${m.label}`);
});

test('#8: a label that already carries a version is left alone (claude 2.1.284 rows)', () => {
  const r = probeModelCatalog({ ...CLAUDE_PROBE, description_key: 'description' }, [], execReturning(CLAUDE_STDOUT));
  assert.deepEqual(r.models.map((m) => m.label), ['Opus 5.5', 'Fable 5.1']);
});

test('#8: with no usable description, the resolved id goes beside the label', async () => {
  const { versionedLabel } = await import('./model-catalog.js');
  assert.equal(versionedLabel({ label: 'Sonnet', resolved: 'claude-sonnet-5' }), 'Sonnet (claude-sonnet-5)');
  assert.equal(versionedLabel({ label: 'Sonnet', description: 'Efficient · cheap', resolved: 'claude-sonnet-5' }), 'Sonnet (claude-sonnet-5)');
  assert.equal(versionedLabel({ label: 'Sonnet' }), 'Sonnet');
  assert.equal(versionedLabel({ label: 'GPT-6-Astra' }), 'GPT-6-Astra');
  assert.equal(versionedLabel({ label: 'Opus (1M context)', resolved: 'claude-opus-5[1m]' }), 'Opus (1M context) (claude-opus-5[1m])');
  assert.equal(versionedLabel({ label: 'Big (128K context)', description: 'Big 2.1 · x' }), 'Big 2.1');
});
