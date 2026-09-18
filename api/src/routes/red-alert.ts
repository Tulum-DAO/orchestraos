/**
 * POST /api/red-alert/report — the report button (deliverable 6, the operator 00:10 Tulum 2026-09-18):
 * "that whole alert system for crashes and bugs and improvements and suggestions should be
 * accessible from a new button on screen … the gateway to the ticket system."
 *
 * Body: { seat, kind: 'crash'|'bug'|'improvement'|'suggestion', words }
 * Runs `scripts/red_alert.py report --reported-by user --channel dashboard --kind … --surface`
 * which captures the evidence itself (pane snapshot, ps STAT, log tail, registry row) and
 * surfaces it: crash/bug -> card + Telegram + Arturo; improvement/suggestion -> Telegram + Arturo.
 * GET /api/red-alert/reports?status=open lists reports (the ticket list).
 *
 * The handler is exported with injectable `run` so tests never spawn python.
 */
import { Router, type Request, type Response } from 'express';
import { execFile } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const HOME = process.env.HOME || '';
// DATA dir (reports land here) vs CODE root (the script lives here): under `orchestra up` they differ.
// Clean-clone proof 2026-09-18 (gate7e): resolving the script under ORCHESTRA_DIR gave ENOENT.
const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || join(HOME, 'scripts/agent-orchestra');
const CODE_ROOT = process.env.ORCHESTRA_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const RED_ALERT = join(CODE_ROOT, 'scripts', 'red_alert.py');

export const KINDS = ['crash', 'bug', 'improvement', 'suggestion'] as const;
export type Kind = typeof KINDS[number];

export type Runner = (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>;

const defaultRun: Runner = (args) => new Promise((resolve) => {
  execFile('python3', [RED_ALERT, ...args], { cwd: CODE_ROOT, env: { ...process.env, ORCHESTRA_DIR, ORCHESTRA_ROOT: CODE_ROOT }, timeout: 120_000, maxBuffer: 4 * 1024 * 1024 },
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

const router = Router();
router.post('/report', (req, res) => { void handleReport(req, res); });
router.get('/reports', (req, res) => { void handleList(req, res); });
export default router;
