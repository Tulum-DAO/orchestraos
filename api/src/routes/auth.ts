import { Router, type Request, type Response } from 'express';
import { execFileSync } from 'child_process';
import { readFileSync } from 'fs';
import path from 'path';
import os from 'os';
import { loadConfig } from '../lib/config.js';

const router = Router();

const IS_MAC = os.hostname().includes('MacBook') || os.platform() === 'darwin';
const VPS_HOST = loadConfig().remoteAuthHost;
const SSH_OPTS = ['-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no', '-o', 'IdentitiesOnly=yes', '-i', `${process.env.HOME}/.ssh/id_ed25519`];
const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || loadConfig().dataDir;

/** Run a command on VPS — SSH if on Mac, local if on VPS */
function vpsExec(cmd: string, timeout = 10000): string {
  if (IS_MAC) {
    return execFileSync('ssh', [...SSH_OPTS, VPS_HOST, cmd], { encoding: 'utf-8', timeout });
  }
  return execFileSync('bash', ['-c', cmd], { encoding: 'utf-8', timeout });
}

/** Strip ANSI codes */
function stripAnsi(s: string): string {
  return s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '');
}

/**
 * GET /api/system/vps-auth/gm-state
 * Read GM's tmux output and determine what state it's in.
 * Returns: state + raw output for the UI to display.
 *
 * States:
 *   not_running     — no gm tmux session
 *   needs_login     — shows 401 / "Please run /login" / expired token
 *   login_options   — /login was sent, showing account selection
 *   url_ready       — auth URL is visible, waiting for code paste
 *   code_prompt     — "Paste code here if prompted" visible
 *   authenticated   — GM appears to be working normally
 *   idle            — Claude Code prompt visible, no error
 *   unknown         — can't determine
 */
router.get('/gm-state', (_req: Request, res: Response) => {
  try {
    // Check if GM session exists
    try {
      vpsExec(`tmux has-session -t gm 2>/dev/null`);
    } catch {
      res.json({ state: 'not_running', output: '' });
      return;
    }

    const raw = vpsExec(`tmux capture-pane -t gm -p -S -40 2>/dev/null || echo ""`);
    const clean = stripAnsi(raw);
    const output = clean.trim().split('\n').slice(-25).join('\n');

    // Reconstruct wrapped URLs: tmux wraps long URLs across multiple indented lines.
    // Example:
    //   https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d
    //   9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.c
    //   laude.com%2Foauth%2Fcode%2Fcallback&scope=...
    const lines = clean.split('\n');
    let urlParts: string[] = [];
    let collecting = false;
    for (const line of lines) {
      const trimmed = line.trim();
      if (/^https:\/\//.test(trimmed)) {
        // Start of a URL
        collecting = true;
        urlParts = [trimmed];
      } else if (collecting) {
        // Continuation: indented line with URL-safe chars, no prompt symbols
        if (/^[a-zA-Z0-9%&=_.~:/?#@!$'()*+,;-]/.test(trimmed) && !/^[❯›>✻●⎿─]/.test(trimmed) && trimmed.length > 5) {
          urlParts.push(trimmed);
        } else {
          collecting = false;
        }
      }
    }
    const fullUrl = urlParts.length > 0 ? urlParts.join('') : null;
    const urlMatch = fullUrl && /claude\.com|claude\.ai|anthropic\.com/.test(fullUrl) ? fullUrl : null;

    // Use the TAIL of the output for state detection so old errors don't override newer status
    const tail = clean.trim().split('\n').slice(-15).join('\n');

    // Login successful — needs Enter to continue (highest priority)
    if (/Login successful|Logged in as/i.test(tail) && /Press Enter to continue/i.test(tail)) {
      res.json({ state: 'login_success', output });
      return;
    }

    // Check for "Paste code here"
    if (/Paste code here/i.test(tail) && urlMatch) {
      res.json({ state: 'code_prompt', url: urlMatch, output });
      return;
    }

    // URL visible but maybe no paste prompt yet
    if (urlMatch && /oauth|authorize|login/i.test(urlMatch) && /sign in|url below/i.test(tail)) {
      res.json({ state: 'url_ready', url: urlMatch, output });
      return;
    }

    // Login method selection visible
    if (/Select login method/i.test(tail) || /Claude account with subscription/i.test(tail)) {
      res.json({ state: 'login_options', output });
      return;
    }

    // Needs login — 401, expired token, "Please run /login" (only if in tail)
    if (/OAuth token has expired|authentication_error|Please run \/login/i.test(tail)) {
      res.json({ state: 'needs_login', output });
      return;
    }

    // Check for normal working state
    if (/thinking|running|tool|reading|writing|searching/i.test(tail)) {
      res.json({ state: 'authenticated', output });
      return;
    }

    // Idle Claude Code prompt (❯ with no error)
    if (/❯\s*$/.test(clean) && !/error|expired|401/i.test(clean)) {
      res.json({ state: 'idle', output });
      return;
    }

    res.json({ state: 'unknown', output });
  } catch (err: any) {
    res.status(500).json({ error: err.message, output: '' });
  }
});

/**
 * POST /api/system/vps-auth/send-login
 * Send /login to the GM's tmux session.
 */
router.post('/send-login', (_req: Request, res: Response) => {
  try {
    vpsExec(`tmux send-keys -t gm '/login' Enter`);
    res.json({ sent: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * POST /api/system/vps-auth/select-option
 * Send Enter (or a specific key) to select an option in the login flow.
 * Body: { key?: string } — defaults to Enter (selects highlighted option)
 */
router.post('/select-option', (req: Request, res: Response) => {
  const { key } = req.body || {};
  try {
    vpsExec(`tmux send-keys -t gm '${key || ''}' Enter`);
    res.json({ sent: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * POST /api/system/vps-auth/submit-code
 * Paste the auth code into the GM's tmux session.
 * The code goes into the "Paste code here if prompted >" field.
 */
router.post('/submit-code', (req: Request, res: Response) => {
  const { code } = req.body;
  if (!code || typeof code !== 'string') {
    res.status(400).json({ error: 'code required' });
    return;
  }
  const sanitized = code.trim();
  try {
    const escaped = sanitized.replace(/'/g, "'\\''");
    vpsExec(`tmux send-keys -t gm '${escaped}' Enter`);
    res.json({ sent: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * POST /api/system/vps-auth/notify-telegram
 * Send a message to the operator via Telegram Bot API.
 * Body: { message: string } or { url: string }
 */
router.post('/notify-telegram', async (req: Request, res: Response) => {
  const { message, url } = req.body;
  const text = url
    ? `🔑 *GM Auth Required*\n\nOpen this link to authenticate:\n${url}\n\nPaste the code back here or in the dashboard.`
    : message;

  if (!text) {
    res.status(400).json({ error: 'message or url required' });
    return;
  }

  try {
    let botToken = process.env.TELEGRAM_BOT_TOKEN;
    if (!botToken) {
      try {
        const envContent = readFileSync(path.join(ORCHESTRA_DIR, '.env'), 'utf-8');
        const match = envContent.match(/TELEGRAM_BOT_TOKEN=["']?([^\s"']+)/);
        if (match) botToken = match[1];
      } catch {}
    }
    if (!botToken) {
      res.status(500).json({ error: 'TELEGRAM_BOT_TOKEN not found' });
      return;
    }

    let chatId: string | null = null;
    try {
      chatId = readFileSync(path.join(ORCHESTRA_DIR, '.shaw_chat_id'), 'utf-8').trim();
    } catch {}
    if (!chatId) chatId = process.env.SHAW_TELEGRAM_CHAT_ID || null;
    if (!chatId) {
      res.status(500).json({ error: 'the operator chat ID not found' });
      return;
    }

    const telegramUrl = `https://api.telegram.org/bot${botToken}/sendMessage`;
    const telegramRes = await fetch(telegramUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: 'Markdown',
        disable_web_page_preview: true,
      }),
    });
    const telegramData = await telegramRes.json() as any;

    if (telegramData.ok) {
      res.json({ sent: true });
    } else {
      res.status(500).json({ error: 'Telegram API error', detail: telegramData.description });
    }
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
