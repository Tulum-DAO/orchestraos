/**
 * One agent's conversation as a Markdown file to send someone (GET /api/agents/:id/transcript.md).
 *
 * From the transcript route's items: user and agent turns as sections with their times; each tool
 * call as ONE line ("ran `npm test` -> 42 passed"); tool output only with tools='full'; thinking
 * NEVER (the model's scratch, which can quote secrets mid-reasoning). Everything goes through
 * redactSecrets, and the header says how many secrets were removed.
 */
import { redactSecrets } from './redactSecrets.js';

export interface MdItem {
  kind: string;
  role?: string;
  text?: string;
  tool?: string;
  input?: unknown;
  id?: string;
  tool_use_id?: string;
  is_error?: boolean;
  ts?: string;
}

export interface MdOptions {
  agent: string;
  model?: string | null;
  exportedBy?: string | null;
  now?: Date;
  lastTurns?: number;
  tools?: 'summary' | 'full';
  exactSecrets?: string[];
}

export const DEFAULT_LAST_TURNS = 50;
const ONE_LINE = 100;

function oneLine(s: string, n = ONE_LINE): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
}

function inputSummary(tool: string, input: unknown): string {
  const o = (input && typeof input === 'object') ? input as Record<string, unknown> : {};
  const pick = ['command', 'cmd', 'file_path', 'path', 'pattern', 'url', 'query', 'description']
    .map((k) => o[k]).find((v) => typeof v === 'string' && v.trim());
  if (typeof pick === 'string') return oneLine(pick, 80);
  if (typeof input === 'string' && input.trim()) return oneLine(input, 80);
  return '';
}

function resultSummary(text: string | undefined, isError: boolean | undefined): string {
  if (isError) return 'error' + (text ? `: ${oneLine(text, 60)}` : '');
  const first = String(text || '').split('\n').map((l) => l.trim()).find(Boolean);
  return first ? oneLine(first, 60) : 'done';
}

function when(ts?: string): string {
  if (!ts) return '';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? '' : d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
}

/** The items of the last N turns (a turn starts at each user text). */
export function lastTurns(items: MdItem[], n: number): MdItem[] {
  const starts = items.map((it, i) => (it.kind === 'text' && it.role === 'user' ? i : -1)).filter((i) => i >= 0);
  if (starts.length <= n) return items;
  return items.slice(starts[starts.length - n]);
}

export function transcriptMarkdown(items: MdItem[], o: MdOptions): { markdown: string; redacted: number } {
  const now = o.now ?? new Date();
  const tools = o.tools ?? 'summary';
  const n = o.lastTurns ?? DEFAULT_LAST_TURNS;
  const picked = lastTurns(items.filter((i) => i.kind !== 'thinking'), n);
  const results = new Map<string, MdItem>();
  for (const it of picked) if (it.kind === 'tool_result' && it.tool_use_id) results.set(it.tool_use_id, it);

  // Redact every raw piece BEFORE it is shortened: a key cut in half no longer matches its shape.
  const kinds: Record<string, number> = {};
  const clean = (t: string | undefined): string => {
    const r = redactSecrets(String(t ?? ''), o.exactSecrets ?? []);
    for (const [k, v] of Object.entries(r.kinds)) kinds[k] = (kinds[k] || 0) + v;
    return r.text;
  };
  const cleanInput = (input: unknown): unknown => {
    if (typeof input === 'string') return clean(input);
    if (input && typeof input === 'object') {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(input as Record<string, unknown>)) out[k] = typeof v === 'string' ? clean(v) : v;
      return out;
    }
    return input;
  };
  const body: string[] = [];
  let lastRole = '';
  for (const it of picked) {
    if (it.kind === 'text' && it.text && it.text.trim()) {
      const who = it.role === 'user' ? 'You' : o.agent;
      if (who !== lastRole || it.ts) body.push(`\n### ${who}${it.ts ? ` · ${when(it.ts)}` : ''}\n`);
      body.push(clean(it.text).trim() + '\n');
      lastRole = who;
    } else if (it.kind === 'tool_use') {
      const res = it.id ? results.get(it.id) : undefined;
      const what = inputSummary(String(it.tool || 'tool'), cleanInput(it.input));
      const resText = res?.text ? clean(res.text) : undefined;
      body.push(`- ran \`${it.tool || 'tool'}\`${what ? ` \`${what.replace(/`/g, "'")}\`` : ''} → ${resultSummary(resText, res?.is_error)}`);
      if (tools === 'full' && resText) {
        body.push('\n  ```\n' + resText.split('\n').map((l) => '  ' + l).join('\n') + '\n  ```');
      }
      lastRole = '';
    }
  }

  const turns = picked.filter((i) => i.kind === 'text' && i.role === 'user').length;
  const red = { text: body.join('\n'), kinds, count: Object.values(kinds).reduce((a, b) => a + b, 0) };
  const head = [
    `# ${o.agent}: conversation`,
    '',
    `- **Agent:** ${o.agent}${o.model ? ` (${o.model})` : ''}`,
    `- **Covers:** the last ${turns} turn${turns === 1 ? '' : 's'}${picked[0]?.ts ? `, from ${when(picked[0].ts)}` : ''}`,
    `- **Exported:** ${when(now.toISOString())}${o.exportedBy ? ` by ${o.exportedBy}` : ''}`,
    `- **Tool calls:** ${tools === 'full' ? 'tool output included' : 'one line each, output left out'}; the model's thinking is never included`,
    `- **Secrets removed:** ${red.count}${red.count ? ` (${Object.entries(red.kinds).map(([k, v]) => `${v} ${k}`).join(', ')})` : ''}`,
    '',
    '---',
  ];
  return { markdown: head.join('\n') + '\n' + red.text.trim() + '\n', redacted: red.count };
}
