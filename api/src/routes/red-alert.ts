/**
 * POST /api/red-alert/report — operator report button.
 * GET  /api/red-alert/reports — ticket list.
 * POST /api/red-alert/reports/:id/issue/preview — scrubbed GitHub issue preview (never files).
 * POST /api/red-alert/reports/:id/issue — file after human confirm (create or comment on open fp).
 *
 * Target repo: orchestra.toml [github_issues] repo (default unset). Never auto-files.
 */
import { Router, type Request, type Response } from 'express';
import { execFile } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { dataDir } from '../lib/config.js';

const ORCHESTRA_DIR = dataDir();
const CODE_ROOT = process.env.ORCHESTRA_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const RED_ALERT = join(CODE_ROOT, 'scripts', 'red_alert.py');
const RED_ALERT_ISSUE = join(CODE_ROOT, 'scripts', 'red_alert_issue.py');

export const KINDS = ['crash', 'bug', 'improvement', 'suggestion'] as const;
export type Kind = typeof KINDS[number];

export type Runner = (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>;

const defaultRun: Runner = (args) => new Promise((resolve) => {
  execFile('python3', [RED_ALERT, ...args], { cwd: CODE_ROOT, env: { ...process.env, ORCHESTRA_DIR, ORCHESTRA_ROOT: CODE_ROOT }, timeout: 120_000, maxBuffer: 4 * 1024 * 1024 },
    (err, stdout, stderr) => resolve({ code: err ? ((err as any).code ?? 1) : 0, stdout: String(stdout), stderr: String(stderr) }));
});

const defaultIssueRun: Runner = (args) => new Promise((resolve) => {
  execFile('python3', [RED_ALERT_ISSUE, ...args], { cwd: CODE_ROOT, env: { ...process.env, ORCHESTRA_DIR, ORCHESTRA_ROOT: CODE_ROOT }, timeout: 120_000, maxBuffer: 4 * 1024 * 1024 },
    (err, stdout, stderr) => resolve({ code: err ? ((err as any).code ?? 1) : 0, stdout: String(stdout), stderr: String(stderr) }));
});

export const CHANNELS = ['dashboard', 'ios', 'watch', 'web'] as const;

export function validate(body: any): { ok: true; seat: string; kind: Kind; words: string; channel: string } | { ok: false; error: string } {
  const seat = String(body?.seat || '').trim();
  const channel = (CHANNELS as readonly string[]).includes(String(body?.channel || '')) ? String(body.channel) : 'dashboard';
  const kind = String(body?.kind || '').trim() as Kind;
  const words = String(body?.words || '').trim();
  if (!seat || !/^[A-Za-z0-9._-]{1,64}$/.test(seat)) return { ok: false, error: 'seat required (letters, digits, . _ -)' };
  if (!KINDS.includes(kind)) return { ok: false, error: `kind must be one of ${KINDS.join('|')}` };
  if (words.length < 3) return { ok: false, error: 'say what you saw (3+ characters)' };
  if (words.length > 2000) return { ok: false, error: 'keep it under 2000 characters' };
  return { ok: true, seat, kind, words, channel };
}

export async function handleReport(req: Request, res: Response, run: Runner = defaultRun): Promise<void> {
  const v = validate(req.body);
  if (!v.ok) { res.status(400).json({ error: v.error }); return; }
  const r = await run(['report', '--reported-by', 'user', '--channel', v.channel, '--kind', v.kind,
    '--seat', v.seat, '--symptom', v.words, '--surface']);
  const last = r.stdout.trim().split('\n').pop() || '';
  let parsed: any = null;
  try { parsed = JSON.parse(last); } catch { /* not json */ }
  if (r.code !== 0 || !parsed?.id) {
    res.status(500).json({ error: 'report failed', detail: (r.stderr || r.stdout).slice(-400) });
    return;
  }
  res.json({ ok: true, ...parsed });
}

export async function handleList(req: Request, res: Response, run: Runner = defaultRun): Promise<void> {
  const status = String(req.query.status || '');
  const args = ['list', '--json'];
  if (status) args.push('--status', status);
  const r = await run(args);
  try { res.json(JSON.parse(r.stdout || '[]')); } catch { res.status(500).json({ error: 'list failed', detail: r.stderr.slice(-400) }); }
}

function parseIssueJson(stdout: string): any {
  const text = stdout.trim();
  // Preview/create print one JSON object; prefer whole-stdout parse so nested
  // braces inside "body" do not truncate the document.
  try { return JSON.parse(text); } catch { /* fall through */ }
  const start = text.indexOf('{');
  if (start < 0) return null;
  try { return JSON.parse(text.slice(start)); } catch { return null; }
}

export async function handleIssuePreview(req: Request, res: Response, run: Runner = defaultIssueRun): Promise<void> {
  const id = String(req.params.id || '').trim();
  if (!id) { res.status(400).json({ error: 'report id required' }); return; }
  const r = await run(['preview', id]);
  const parsed = parseIssueJson(r.stdout);
  if (!parsed) {
    res.status(500).json({ error: 'preview failed', detail: (r.stderr || r.stdout).slice(-400) });
    return;
  }
  res.status(parsed.ok === false ? 400 : 200).json(parsed);
}

export async function handleIssueCreate(req: Request, res: Response, run: Runner = defaultIssueRun): Promise<void> {
  const id = String(req.params.id || '').trim();
  if (!id) { res.status(400).json({ error: 'report id required' }); return; }
  // Human confirm required: body.confirm === true. Never auto-file.
  if (req.body?.confirm !== true) {
    res.status(400).json({ error: 'confirm:true required — never auto-files; show preview first' });
    return;
  }
  const dry = req.body?.dry_run === true;
  const args = ['create', id, '--yes'];
  if (dry) args.push('--dry-run');
  const r = await run(args);
  const parsed = parseIssueJson(r.stdout);
  if (!parsed) {
    res.status(500).json({ error: 'create failed', detail: (r.stderr || r.stdout).slice(-400) });
    return;
  }
  if (!parsed.ok) {
    res.status(400).json(parsed);
    return;
  }
  res.json(parsed);
}

const router = Router();
router.post('/report', (req, res) => { void handleReport(req, res); });
router.get('/reports', (req, res) => { void handleList(req, res); });
router.post('/reports/:id/issue/preview', (req, res) => { void handleIssuePreview(req, res); });
router.post('/reports/:id/issue', (req, res) => { void handleIssueCreate(req, res); });
export default router;
