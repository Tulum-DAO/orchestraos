/**
 * WebSocket terminal — uses node-pty to attach to tmux sessions with a real PTY.
 * xterm.js in the browser gets a true terminal experience.
 */

import type { IncomingMessage } from 'http';
import type { WebSocket as WsType, WebSocketServer as WssType } from 'ws';

// Lazy-load node-pty to avoid crash if not installed
import { createRequire } from 'module';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { CODE_ROOT } from '../lib/codeRoot.js';
import { loadConfig } from '../lib/config.js';
const require_cjs = createRequire(import.meta.url);

let pty: any;
try {
  pty = require_cjs('node-pty');
  console.log('[terminal] node-pty loaded successfully');
} catch (e: any) {
  console.warn('[terminal] node-pty not available:', e.message);
}

export function setupTerminalWebSocket(wss: WssType) {
  if (!pty) return;

  wss.on('connection', (ws: WsType, req: IncomingMessage) => {
    const url = new URL(req.url || '/', `http://${req.headers.host}`);
    const session = url.searchParams.get('session');

    if (!session) {
      ws.send('\r\n\x1b[31mError: ?session= required\x1b[0m\r\n');
      ws.close();
      return;
    }
    if (!isRegisteredTerminalSession(session)) {
      ws.send('\r\n\x1b[31mError: registered session required\x1b[0m\r\n');
      ws.close();
      return;
    }

    console.log(`[terminal] PTY attaching to tmux session: ${session}`);
    refreshSessionDefaults(session);

    // Use node-pty to spawn tmux attach with a real PTY.
    // -f ignore-size (tmux >=3.2; box runs 3.4): this client must NOT drive the
    // shared window's size. Default window-size=latest meant a phone-Safari
    // viewer reflowed every other client AND the pane text the state detector
    // parses (agent-state-truth audit F8, 2026-08-09). Small viewers see a
    // clipped viewport of the true-size window instead of resizing it.
    const term = pty.spawn('tmux', ['attach', '-t', session, '-f', 'ignore-size'], {
      name: 'xterm-256color',
      cols: 80,
      rows: 24,
      cwd: process.env.HOME,
      env: { ...process.env, TERM: 'xterm-256color' },
    });

    console.log(`[terminal] PTY pid=${term.pid} for ${session}`);

    // PTY output → WebSocket
    term.onData((data: string) => {
      if (ws.readyState === 1) {
        ws.send(data);
      }
    });

    // WebSocket input → PTY
    ws.on('message', (data: Buffer | string) => {
      const msg = data.toString();

      // Check for resize control messages
      if (msg.startsWith('{')) {
        try {
          const ctrl = JSON.parse(msg);
          if (ctrl.type === 'resize' && ctrl.cols && ctrl.rows) {
            term.resize(ctrl.cols, ctrl.rows);
            return;
          }
        } catch {
          // Not JSON, treat as keystroke
        }
      }

      // Regular keystroke
      term.write(msg);
    });

    const cleanup = () => {
      console.log(`[terminal] Cleanup ${session}`);
      try { term.kill(); } catch {}
    };

    ws.on('close', cleanup);
    ws.on('error', cleanup);

    term.onExit(({ exitCode }: { exitCode: number }) => {
      console.log(`[terminal] PTY exited for ${session} (code ${exitCode})`);
      if (ws.readyState === 1) {
        ws.send('\r\n\x1b[33m[Session ended]\x1b[0m\r\n');
        ws.close();
      }
    });
  });
}

export function isRegisteredTerminalSession(session: string): boolean {
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(session)) return false;
  try {
    const registry = JSON.parse(readFileSync(path.join(loadConfig().dataDir, 'registry.json'), 'utf8'));
    return Object.values((registry?.agents || {}) as Record<string, any>)
      .some((agent: any) => agent && (agent.tmux_session === session || agent.name === session));
  } catch { return false; }
}

/** Re-apply scripts/tmux_session_defaults.sh to a seat before attaching. A seat gets its hooks at
 *  spawn, so a fix to them (2026-10-11: the client-detached hook printed "(null):0: not in a mode"
 *  on every other Dev open) reached a running seat only on its next spawn. Idempotent, fail-soft,
 *  and only for names the helper itself accepts. */
export function refreshSessionDefaults(session: string): void {
  if (!/^[A-Za-z0-9_-]+$/.test(session)) return;
  const helper = path.join(CODE_ROOT, 'scripts', 'tmux_session_defaults.sh');
  try {
    execFileSync('bash', ['-c', 'source "$1" && orch_tmux_session_defaults "$2"', '_', helper, session], { timeout: 5000, stdio: 'ignore' });
  } catch { /* never block an attach on it */ }
}
