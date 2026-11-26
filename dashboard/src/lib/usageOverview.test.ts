import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { hasAccountChoice, resetsText, toneFor, usageOverview } from './usageOverview.ts';

const NOW = new Date('2026-11-21T15:00:00Z');
const at = (iso: string) => Date.parse(iso) / 1000;
const rows = [
  { provider: 'grok', reason: 'Grok reports no usage we can read' },
  { provider: 'claude', account: 'work', window: 'weekly', used_pct: 0, resets_at: at('2026-11-24T09:59:00Z'), stale: false },
  { provider: 'claude', account: 'default', window: 'weekly', used_pct: 10, resets_at: at('2026-11-23T21:00:00Z'), stale: false },
  { provider: 'claude', account: 'default', window: '5h', used_pct: 3, resets_at: at('2026-11-21T17:14:00Z'), stale: false },
  { provider: 'claude', account: 'work', window: '5h', used_pct: 2, resets_at: at('2026-11-21T19:29:00Z'), stale: false },
  { provider: 'codex', account: 'default', window: 'weekly', used_pct: 94, resets_at: at('2026-11-23T14:32:00Z'), stale: false },
  { provider: 'gemini', reason: 'Gemini CLI reports no usage we can read' },
];

test('providers in rail order; a provider with no reading is a muted line, never a 0% bar', () => {
  const v = usageOverview(rows, NOW);
  assert.deepEqual(v.providers.map((p) => p.id), ['claude', 'codex']);
  assert.deepEqual(v.silent.map((s) => s.name), ['Gemini', 'Grok']);
});

test('the agents’ login first, labelled, with its windows shortest first; the ring is the rail’s (agents’ login only)', () => {
  const [claude] = usageOverview(rows, NOW).providers;
  assert.deepEqual(claude.accounts.map((a) => [a.label, a.agents]), [['Main login', true], ['work', false]]);
  assert.deepEqual(claude.accounts[0].windows.map((w) => [w.label, w.pct]), [['5-hour session', 3], ['Weekly', 10]]);
  assert.equal(claude.ring?.pct, 10);
  assert.equal(claude.ring?.tone, 'ok');
});

test('colour by how close a window is to full; the ring takes it too', () => {
  assert.deepEqual([toneFor(0), toneFor(69), toneFor(70), toneFor(89), toneFor(90), toneFor(100)], ['ok', 'ok', 'warn', 'warn', 'full', 'full']);
  const codex = usageOverview(rows, NOW).providers[1];
  assert.equal(codex.accounts[0].windows[0].tone, 'full');
  assert.equal(codex.ring?.tone, 'full');
});

test('resets: a countdown within a day, else the day and time', () => {
  assert.equal(resetsText(at('2026-11-21T17:14:00Z'), NOW), 'Resets in 2 h 14 m');
  assert.equal(resetsText(at('2026-11-21T15:20:00Z'), NOW), 'Resets in 20 m');
  assert.equal(resetsText(at('2026-11-21T15:00:30Z'), NOW), 'Resets now');
  assert.match(resetsText(at('2026-11-23T21:00:00Z'), NOW), /^Resets (Mon|Sun|Tue) /);
});

test('a stale reading says how old it is; garbage in is no providers, not a crash', () => {
  const v = usageOverview([{ provider: 'claude', account: 'default', window: '5h', used_pct: 40, stale: true, as_of: at('2026-11-21T14:05:00Z') }], NOW);
  assert.match(v.providers[0].accounts[0].windows[0].stale ?? '', /^as of /);
  assert.deepEqual(usageOverview(null, NOW), { providers: [], silent: [] });
  assert.deepEqual(usageOverview([null, 5, { nope: 1 }], NOW), { providers: [], silent: [] });
});

// ── the rendered page (components/settings/sections/UsageOverview.tsx), from a primed query cache: no browser ──

async function renderPanel(rows: unknown, props: Record<string, unknown> = {}, hidden = false): Promise<string> {
  const { register } = await import('node:module');
  // the panel takes ProviderMark from the model picker, whose module pulls in the whole app: a stand-in mark here
  register('data:text/javascript,' + encodeURIComponent(
    "export async function load(url, ctx, next) {" +
    " if (url.endsWith('/components/kit/ModelPicker.tsx')) return { format: 'module', shortCircuit: true," +
    "   source: 'export function ProviderMark() { return null; }' };" +
    " return next(url, ctx); }"));
  const React = await import('react');
  (globalThis as { React?: unknown }).React = React;
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query');
  const { UsageOverviewPanel } = await import('../components/settings/sections/UsageOverview.tsx');
  const { USAGE_QUERY_KEY } = await import('./usageLine.ts');
  const qc = new QueryClient();
  if (hidden) {
    // the 404 a non-admin gets: the query in its error state with UsageHidden
    const { UsageHidden } = await import('./usageLine.ts');
    qc.setQueryDefaults(USAGE_QUERY_KEY, { retryOnMount: false });   // keep the error on mount, as a settled 404 is
    qc.getQueryCache().build(qc, { queryKey: USAGE_QUERY_KEY }).setState({ status: 'error', error: new UsageHidden(), fetchStatus: 'idle' });
  } else {
    qc.setQueryData(USAGE_QUERY_KEY, rows);
  }
  return renderToStaticMarkup(React.createElement(QueryClientProvider, { client: qc }, React.createElement(UsageOverviewPanel, props)));
}

test('no reading at all (polling off, no status line yet): each provider says "No reading yet" with why; no bar, no 0%', async () => {
  // what scripts/usage_meter.py usage_rows returns on a fresh install
  const html = await renderPanel([
    { provider: 'claude', account: null, window: null, used_pct: null, reason: 'Claude Code has not reported usage yet' },
    { provider: 'codex', account: null, window: null, used_pct: null, reason: 'no Codex session has reported its limits yet' },
  ]);
  assert.match(html, /data-usage-silent-provider="claude"[^>]*>.*Claude.*No reading yet · Claude Code has not reported usage yet/);
  assert.match(html, /data-usage-silent-provider="codex"[^>]*>.*No reading yet · no Codex session/);
  assert.doesNotMatch(html, /role="progressbar"/);
  assert.doesNotMatch(html, /0% used/);
  // every line carries data-setting, or settings.css hides the whole group
  assert.match(html, /data-setting="usage:silent:claude"/);
});

test('one login: a card with a bar per window, no account switch anywhere', async () => {
  const html = await renderPanel([
    { provider: 'claude', account: 'default', window: '5h', used_pct: 42, resets_at: at('2026-11-21T17:14:00Z'), stale: false },
    { provider: 'claude', account: 'default', window: 'weekly', used_pct: 71, stale: false },
  ]);
  assert.match(html, /data-usage-provider="claude"/);
  assert.match(html, /data-usage-window="5-hour session"[^>]*data-tone="ok"/);
  assert.match(html, /data-usage-window="Weekly"[^>]*data-tone="warn"/);
  assert.match(html, /aria-valuenow="42"/);
  assert.doesNotMatch(html, /data-agents-badge|data-account-switch|data-use-for-all/);
});

test('Settings › Usage is wired: the route lists it and the shell renders UsageSection for it', () => {
  const shell = readFileSync(new URL('../components/settings/SettingsShell.tsx', import.meta.url), 'utf8');
  assert.match(shell, /import \{ UsageSection \} from '\.\/sections\/UsageSection';/);
  assert.match(shell, /case 'usage': return <UsageSection \/>;/);
});

test('one card per account: one account shows one card and offers no choice; two accounts are two cards', () => {
  const [claude, codex] = usageOverview(rows, NOW).providers;
  assert.equal(hasAccountChoice(claude), true);
  assert.equal(claude.accounts.length, 2);
  assert.equal(hasAccountChoice(codex), false);
  assert.equal(codex.accounts.length, 1);
  assert.equal(hasAccountChoice({ accounts: [] }), false);
});

test('a per-account action shows under each card only where there is a choice (more than one account)', async () => {
  const asked: string[] = [];
  const accountAction = (p: { id: string }, a: { id: string }) => { asked.push(`${p.id}:${a.id}`); return `act-${a.id}`; };
  const html = await renderPanel(rows, { accountAction });
  assert.deepEqual(asked, ['claude:default', 'claude:work'], 'asked for the Claude cards only: Codex has one account');
  assert.match(html, /data-usage-account="default"[^]*data-usage-account-action="default"[^>]*>act-default</);
  assert.match(html, /data-usage-account="work"[^]*data-usage-account-action="work"[^>]*>act-work</);
  assert.equal((html.match(/data-usage-account-action=/g) ?? []).length, 2);
  // no action given (this build): no action slot anywhere
  assert.doesNotMatch(await renderPanel(rows), /data-usage-account-action=/);
  // an action that returns nothing for a card leaves no empty slot
  assert.doesNotMatch(await renderPanel(rows, { accountAction: () => null }), /data-usage-account-action=/);
});

test('before the first reading, with polling on: each polled provider says "First reading in a moment" with the off switch beside it', async () => {
  const html = await renderPanel([
    { provider: 'claude', account: null, window: null, used_pct: null, reason: 'first reading in a moment', pending: true },
    { provider: 'codex', account: null, window: null, used_pct: null, reason: 'first reading in a moment', pending: true },
    { provider: 'gemini', reason: 'Gemini CLI reports no usage we can read' },
  ]);
  assert.match(html, /data-usage-pending="claude"[^]*First reading in a moment[^]*data-usage-off-switch[^>]*>[^]*\[usage\] poll = false/);
  assert.match(html, /data-usage-pending="codex"/);
  assert.match(html, /data-setting="usage:pending:claude"/);
  assert.doesNotMatch(html, /role="progressbar"/);
  // a provider the poller does not read keeps its plain line
  assert.match(html, /data-usage-silent-provider="gemini"[^>]*>.*No reading yet · Gemini/);
  assert.doesNotMatch(html, /data-usage-silent-provider="claude"/);
});

test('the terms note: no documented subscription API, the terms linked, the Admin API is API-key only, off in one line', async () => {
  const html = await renderPanel(rows);
  assert.match(html, /data-usage-terms/);
  assert.match(html, /no\s+documented usage API/);
  assert.match(html, /Claude Code&#x27;s own <code>\/usage<\/code>/);
  for (const href of ['https://www.anthropic.com/legal/consumer-terms', 'https://code.claude.com/docs/en/legal-and-compliance',
    'https://openai.com/policies/row-terms-of-use/', 'https://platform.claude.com/docs/en/manage-claude/usage-cost-api']) {
    assert.ok(html.includes(`href="${href}"`), href);
  }
  assert.match(html, /without prior notice/);
  assert.match(html, /API-key organizations only/);
  assert.match(html, /\[usage\] poll = false/);
});

test('a non-admin (the API\'s 404) gets one sentence, never the numbers', async () => {
  const html = await renderPanel(null, {}, true);
  assert.match(html, /data-usage-admin-only[^>]*>Only an admin of this install can see plan usage\./);
  assert.doesNotMatch(html, /data-usage-overview|data-usage-terms/);
});

test('the agents\' login is the one the rows mark, not the literal "default": first, badged, and the ring\'s', () => {
  const marked = [
    { provider: 'claude', account: 'personal', window: 'weekly', used_pct: 95, stale: false, agents: false },
    { provider: 'claude', account: 'work', window: 'weekly', used_pct: 12, stale: false, agents: true },
  ];
  const [claude] = usageOverview(marked, NOW).providers;
  assert.deepEqual(claude.accounts.map((a) => [a.id, a.agents]), [['work', true], ['personal', false]]);
  assert.equal(claude.ring?.pct, 12, 'the agents\' login, not the fuller one');
  // no row marked (an older server): the default login, as before
  const [legacy] = usageOverview(rows, NOW).providers;
  assert.equal(legacy.accounts[0].id, 'default');
});

test('the Fable weekly allowance is its own bar, labelled, right after the weekly one', () => {
  const fable = [...rows,
    { provider: 'claude', account: 'default', window: 'weekly-fable', used_pct: 86, resets_at: at('2026-12-01T10:00:00Z'), stale: false }];
  const [claude] = usageOverview(fable, NOW).providers;
  assert.deepEqual(claude.accounts[0].windows.map((w) => [w.label, w.pct]),
    [['5-hour session', 3], ['Weekly', 10], ['Weekly · Fable', 86]]);
});
