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
  assert.deepEqual(got.models.map((m) => m.id), ['default', 'claude-fable-5-1']);
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
  const unknown = got.models.find((m) => m.id === 'default');
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
