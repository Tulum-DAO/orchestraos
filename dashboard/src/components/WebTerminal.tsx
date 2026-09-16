/**
 * WebTerminal — real terminal via xterm.js + WebSocket.
 * Scroll uses tmux copy-mode. Typing auto-exits copy-mode (Escape + keystroke).
 * No manual Esc needed.
 */

import { useEffect, useRef, useCallback } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { useRecentAgents } from '../stores/recentAgents';

interface WebTerminalProps {
  session: string;
  machine: string;
}

const TMUX_PREFIX = '\x02'; // Ctrl+B
const PAGE_UP = '\x1b[5~';
const PAGE_DOWN = '\x1b[6~';
const ARROW_UP = '\x1b[A';
const ARROW_DOWN = '\x1b[B';

function getWsUrl(session: string, machine: string, cols: number, rows: number): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/terminal?session=${encodeURIComponent(session)}&machine=${encodeURIComponent(machine)}&cols=${cols}&rows=${rows}&_auth=1`;
}

export default function WebTerminal({ session, machine }: WebTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const inCopyMode = useRef(false);

  const connect = useCallback(() => {
    if (!containerRef.current) return;

    const screenW = window.innerWidth;
    const fontSize = screenW < 768 ? 11 : 13;
    const term = new Terminal({
      cursorBlink: true,
      fontSize,
      fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, 'Courier New', monospace",
      theme: {
        background: '#000000',
        foreground: '#4ade80',
        cursor: '#4ade80',
        cursorAccent: '#000000',
        selectionBackground: '#4ade8040',
        black: '#000000',
        red: '#f87171',
        green: '#4ade80',
        yellow: '#fbbf24',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#d4d4d8',
        brightBlack: '#71717a',
        brightRed: '#fca5a5',
        brightGreen: '#86efac',
        brightYellow: '#fde68a',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#67e8f9',
        brightWhite: '#f5f5f7',
      },
      allowProposedApi: true,
      scrollback: 5000,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();

    termRef.current = term;
    fitRef.current = fit;

    const initDims = fit.proposeDimensions();
    const wsUrl = getWsUrl(session, machine, initDims?.cols || 80, initDims?.rows || 24);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      term.write('\x1b[33m[Connected]\x1b[0m\r\n');
      // Single resize on connect — no repeated fit() calls that cause scroll churn
      fit.fit();
      const dims = fit.proposeDimensions();
      if (dims && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
      }
    };

    // Buffer initial history burst, write it all at once to avoid scroll animation
    let initialBurst = true;
    let historyBuffer = '';
    const flushHistory = () => {
      if (historyBuffer) {
        term.write(historyBuffer);
        historyBuffer = '';
      }
      initialBurst = false;
      term.scrollToBottom();
    };
    // After 300ms of connection, flush history and switch to streaming mode
    const historyTimeout = setTimeout(flushHistory, 300);

    ws.onmessage = (event) => {
      if (initialBurst) {
        historyBuffer += event.data;
      } else {
        term.write(event.data);
      }
    };

    ws.onclose = () => {
      term.write('\r\n\x1b[31m[Disconnected]\x1b[0m\r\n');
    };

    ws.onerror = () => {
      term.write('\r\n\x1b[31m[Connection error]\x1b[0m\r\n');
    };

    // ── Helper: exit copy-mode ──────────────────────────────────
    const exitCopyMode = () => {
      if (inCopyMode.current && ws.readyState === WebSocket.OPEN) {
        ws.send('\x1b'); // Escape exits tmux copy-mode
        inCopyMode.current = false;
      }
    };

    // ── Helper: enter copy-mode ─────────────────────────────────
    const enterCopyMode = () => {
      if (!inCopyMode.current && ws.readyState === WebSocket.OPEN) {
        ws.send(TMUX_PREFIX + '[');
        inCopyMode.current = true;
      }
    };

    // ── Scroll via tmux copy-mode ───────────────────────────────
    const container = containerRef.current;

    // Desktop wheel scroll
    const handleWheel = (e: WheelEvent) => {
      if (!container.contains(e.target as Node)) return;
      e.preventDefault();
      e.stopPropagation();
      if (ws.readyState !== WebSocket.OPEN) return;

      enterCopyMode();
      const lines = Math.max(1, Math.round(Math.abs(e.deltaY) / 20));
      const key = e.deltaY < 0 ? ARROW_UP : ARROW_DOWN;
      if (lines >= 10) {
        const pages = Math.ceil(lines / 20);
        for (let i = 0; i < pages; i++) ws.send(e.deltaY < 0 ? PAGE_UP : PAGE_DOWN);
      } else {
        for (let i = 0; i < lines; i++) ws.send(key);
      }
    };
    document.addEventListener('wheel', handleWheel, { capture: true, passive: false });

    // Mobile touch scroll
    // Edge-zone exemption: touches starting within EDGE_PX of either screen
    // edge belong to the agent-switch swipe gesture (AgentCard locked-deck
    // deck navigation) — the terminal must fully ignore them, otherwise every
    // edge swipe also drags tmux into copy-mode scroll. Keep in sync with
    // EDGE_PX in AgentCard.tsx.
    // Asymmetric: left is wider because Safari's native back-swipe owns the
    // outer ~25px there — the operator starts left swipes inboard of it. Keep in sync
    // with EDGE_RIGHT_PX / EDGE_LEFT_PX in AgentCard.tsx.
    const EDGE_RIGHT_PX = 28;
    const EDGE_LEFT_PX = 72;
    let touchStartY = 0;
    let edgeTouch = false;
    const handleTouchStart = (e: TouchEvent) => {
      if (!container.contains(e.target as Node)) return;
      const x = e.touches[0].clientX;
      edgeTouch = x < EDGE_LEFT_PX || x > window.innerWidth - EDGE_RIGHT_PX;
      touchStartY = e.touches[0].clientY;
    };
    const handleTouchMove = (e: TouchEvent) => {
      if (!container.contains(e.target as Node)) return;
      if (edgeTouch) return; // edge gesture = agent swipe, never tmux scroll
      e.preventDefault();
      if (ws.readyState !== WebSocket.OPEN) return;

      const deltaY = touchStartY - e.touches[0].clientY;
      touchStartY = e.touches[0].clientY;
      if (Math.abs(deltaY) < 3) return;

      enterCopyMode();
      const lines = Math.max(1, Math.round(Math.abs(deltaY) / 15));
      const direction = deltaY > 0 ? 'up' : 'down';
      if (lines >= 10) {
        const pages = Math.ceil(lines / 20);
        for (let i = 0; i < pages; i++) ws.send(direction === 'up' ? PAGE_UP : PAGE_DOWN);
      } else {
        for (let i = 0; i < lines; i++) ws.send(direction === 'up' ? ARROW_UP : ARROW_DOWN);
      }
    };
    document.addEventListener('touchstart', handleTouchStart, { capture: true, passive: true });
    document.addEventListener('touchmove', handleTouchMove, { capture: true, passive: false });

    // ── Keystrokes: auto-exit copy-mode then send ───────────────
    // Terminal typing = the operator-interaction for the recent-agent chips. This was
    // the missing recency source: agents the operator drives ONLY via the terminal
    // (Full-Audit, orchestra-builder) never appeared in the chips because
    // recency only bumped on ChatInput sends / dev-mode entry. Throttled 30s.
    let lastBump = 0;
    term.onData((data) => {
      const now = Date.now();
      if (now - lastBump > 30_000) {
        lastBump = now;
        try {
          useRecentAgents.getState().bump(session);
        } catch {}
      }
      if (ws.readyState !== WebSocket.OPEN) return;
      if (inCopyMode.current) {
        exitCopyMode();
        setTimeout(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(data);
          }
        }, 80);
        return;
      }
      ws.send(data);
    });

    // ── Fix 1: Exit copy-mode when xterm textarea gets focus ────
    // On mobile, tapping the terminal opens the keyboard (focus event)
    // but doesn't fire onData. Exit copy-mode on focus so typing works.
    const xtermTextarea = container.querySelector('textarea.xterm-helper-textarea') as HTMLElement | null;
    const handleTermFocus = () => { exitCopyMode(); };
    if (xtermTextarea) {
      xtermTextarea.addEventListener('focus', handleTermFocus);
    }

    // ── Resize: fit once on connect, then only on orientation change ──
    const isMobile = window.innerWidth < 768;
    let lastWidth = window.innerWidth;

    const doResize = () => {
      fit.fit();
      const dims = fit.proposeDimensions();
      if (dims && ws.readyState === WebSocket.OPEN) {
        const colAdjust = isMobile ? 1 : 0;
        const cols = Math.max(40, dims.cols - colAdjust);
        ws.send(JSON.stringify({ type: 'resize', cols, rows: dims.rows }));
        if (colAdjust > 0) term.resize(cols, dims.rows);
      }
    };

    // On mobile: only resize on orientation change (width change).
    // Keyboard open/close changes height only — ignore it completely.
    // On desktop: use ResizeObserver normally.
    let resizeObserver: ResizeObserver | null = null;

    if (isMobile) {
      const handleOrientation = () => {
        const newWidth = window.innerWidth;
        if (newWidth !== lastWidth) {
          lastWidth = newWidth;
          doResize();
        }
      };
      window.addEventListener('orientationchange', handleOrientation);
      // Also check on resize but only if width changed
      window.addEventListener('resize', handleOrientation);
    } else {
      resizeObserver = new ResizeObserver(() => doResize());
      resizeObserver.observe(container);
    }

    // Visual viewport: update CSS var so the modal container tracks keyboard
    const updateVvh = () => {
      if (window.visualViewport) {
        document.documentElement.style.setProperty('--vvh', `${window.visualViewport.height}px`);
      }
    };
    window.visualViewport?.addEventListener('resize', updateVvh);
    updateVvh();

    return () => {
      clearTimeout(historyTimeout);
      document.removeEventListener('wheel', handleWheel, { capture: true } as EventListenerOptions);
      document.removeEventListener('touchstart', handleTouchStart, { capture: true } as EventListenerOptions);
      document.removeEventListener('touchmove', handleTouchMove, { capture: true } as EventListenerOptions);
      if (xtermTextarea) {
        xtermTextarea.removeEventListener('focus', handleTermFocus);
      }
      window.visualViewport?.removeEventListener('resize', updateVvh);
      resizeObserver?.disconnect();
      ws.close();
      term.dispose();
    };
  }, [session, machine]);

  useEffect(() => {
    const cleanup = connect();
    return cleanup;
  }, [connect]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-black overscroll-contain"
      style={{ minHeight: '200px', padding: 0, margin: 0 }}
    />
  );
}
