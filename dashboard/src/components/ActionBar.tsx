/**
 * ActionBar — Context-aware agent interaction bar.
 * Sits between chat output and text input.
 * Detects what the agent is prompting for and renders appropriate buttons.
 * Includes Termius-style special key row for mobile tmux interaction.
 */

import { useState, useCallback, useMemo, useRef } from 'react';
import { clsx } from 'clsx';
import { parseAgentOutput, type ParsedPrompt } from '../lib/output-parser';
import { logAction } from '../lib/user-actions';

interface ActionBarProps {
  agentId: string;
  outputLines: string[];
  onInject: (text: string) => void;
  onSendKey: (key: string) => void;
  injectMode: boolean;
  devMode?: boolean;
}

// Special keys for the navigation row
const SPECIAL_KEYS = [
  { label: '↑', key: 'up' },
  { label: '↓', key: 'down' },
  { label: '←', key: 'left' },
  { label: '→', key: 'right' },
  { label: 'Esc', key: 'escape' },
  { label: 'Tab', key: 'tab' },
  { label: '⏎', key: 'enter' },
];

const CTRL_KEYS = [
  { label: '^C', key: 'ctrl-c' },
  { label: '^O', key: 'ctrl-o' },
  { label: '^K', key: 'ctrl-k' },
  { label: '^U', key: 'ctrl-u' },   // was ^Z: it suspended the CLI (RED ALERT ra_2653f9bf)
];

// Keys that interrupt the agent get an "Are you sure?" (Shaw, RED ALERT 2026-09-17).
const CONFIRM_KEYS = new Set(['ctrl-c']);

const KEY_BASE = "text-[11px] px-2 py-1 min-h-[36px] rounded-md font-mono shrink-0 transition-all duration-75";
const KEY_IDLE = "bg-neutral-800 text-neutral-500 hover:text-neutral-300";
const KEY_PRESSED = "bg-neutral-600 text-white scale-90";

export default function ActionBar({ agentId: _agentId, outputLines, onInject, onSendKey, injectMode, devMode: _devMode }: ActionBarProps) {
  const [ctrlActive, setCtrlActive] = useState(false);
  const [pressedKey, setPressedKey] = useState<string | null>(null);
  const [confirmKey, setConfirmKey] = useState<string | null>(null); // ^C needs an 'Are you sure?'
  const pressTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const flash = useCallback((id: string) => {
    setPressedKey(id);
    clearTimeout(pressTimer.current);
    pressTimer.current = setTimeout(() => setPressedKey(null), 150);
  }, []);

  const prompt: ParsedPrompt = useMemo(
    () => parseAgentOutput(outputLines),
    [outputLines]
  );

  // ctrlActive reserved for future sticky modifier use
  void ctrlActive;

  // After any action bar button fires, refocus the terminal textarea
  // so the mobile keyboard stays open.
  const refocusTerminal = useCallback(() => {
    requestAnimationFrame(() => {
      const textarea = document.querySelector('textarea.xterm-helper-textarea') as HTMLElement | null;
      if (textarea) textarea.focus();
    });
  }, []);

  // Wrap handlers to refocus after action
  const wrappedInject = useCallback((text: string) => {
    onInject(text);
    refocusTerminal();
  }, [onInject, refocusTerminal]);

  const wrappedSendKey = useCallback((key: string) => {
    onSendKey(key);
    refocusTerminal();
  }, [onSendKey, refocusTerminal]);

  const wrappedHandleKey = useCallback((key: string) => {
    logAction('actionbar.key', _agentId, key);
    if (ctrlActive && !key.startsWith('ctrl-')) {
      wrappedSendKey(`ctrl-${key}`);
      setCtrlActive(false);
    } else {
      wrappedSendKey(key);
    }
  }, [ctrlActive, wrappedSendKey, _agentId]);

  return (
    <div
      className="space-y-1.5"
    >
      {/* Row 1: Context actions (hidden in dev mode — use raw terminal instead) */}
      {!_devMode && prompt.type === 'permission' && prompt.permissionActions && (
        <div className="flex flex-wrap gap-1.5">
          {prompt.permissionActions.map(action => (
            <button
              key={action}
              onClick={() => wrappedInject(action)}
              className={clsx(
                'text-xs px-3 py-1.5 min-h-[44px] rounded-lg font-medium transition-colors',
                action === 'Allow' || action === 'Always allow'
                  ? 'bg-green-500/15 text-green-400 hover:bg-green-500/25'
                  : 'bg-red-500/15 text-red-400 hover:bg-red-500/25'
              )}
            >
              {action}
            </button>
          ))}
        </div>
      )}

      {!_devMode && prompt.type === 'yes_no' && (
        <div className="flex gap-1.5">
          <button
            onClick={() => wrappedInject('y')}
            className="text-xs px-4 py-1.5 min-h-[44px] rounded-lg font-medium bg-green-500/15 text-green-400 hover:bg-green-500/25 transition-colors"
          >
            Yes
          </button>
          <button
            onClick={() => wrappedInject('n')}
            className="text-xs px-4 py-1.5 min-h-[44px] rounded-lg font-medium bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors"
          >
            No
          </button>
        </div>
      )}

      {!_devMode && prompt.type === 'numbered_options' && prompt.options && (
        <div className="flex flex-wrap gap-1.5">
          {prompt.options.map(opt => (
            <button
              key={opt.num}
              onClick={() => wrappedInject(opt.num)}
              className="bg-neutral-800 text-neutral-300 hover:text-neutral-100 text-xs px-2.5 py-1.5 min-h-[44px] rounded-lg transition-colors text-left"
            >
              <span className="text-neutral-500 mr-1">{opt.num}.</span>
              <span>{opt.text}</span>
            </button>
          ))}
        </div>
      )}

      {!_devMode && prompt.type === 'auth_url' && prompt.authUrl && (
        <a
          href={prompt.authUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 text-xs px-3 py-2.5 min-h-[44px] rounded-lg font-medium bg-amber-500/15 border border-amber-500/30 text-amber-400 hover:bg-amber-500/25 transition-colors w-full"
        >
          <span className="shrink-0">🔑</span>
          <span>Open Auth Link</span>
          <span className="ml-auto text-amber-500/60 text-[10px]">↗</span>
        </a>
      )}

      {/* ^O Expand button removed — use inline "Show details" or the ^O key in the action bar */}

      {confirmKey && (
        <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-neutral-900 border border-neutral-700 rounded-xl p-4 w-72 space-y-3">
            <div className="text-sm text-neutral-100 font-medium">Send {confirmKey === 'ctrl-c' ? '^C' : confirmKey}?</div>
            <div className="text-xs text-neutral-400">This interrupts what the agent is doing right now. Are you sure?</div>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setConfirmKey(null)} className="text-xs px-3 py-1.5 min-h-[40px] rounded-lg bg-neutral-800 text-neutral-300">Cancel</button>
              <button
                onClick={() => { const k = confirmKey; setConfirmKey(null); wrappedSendKey(k); setCtrlActive(false); }}
                className="text-xs px-3 py-1.5 min-h-[40px] rounded-lg bg-red-500/20 text-red-300 font-medium"
              >
                Yes, send it
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Row 2: Special keys (visible in inject mode) */}
      {injectMode && (
        <div className="flex gap-1 overflow-x-auto pb-0.5 scrollbar-none">
          {/* Shift+Tab (cycle permissions mode in Claude Code) */}
          <button
            onClick={() => { flash('shift-tab'); wrappedSendKey('shift-tab'); }}
            className={clsx(KEY_BASE, pressedKey === 'shift-tab' ? KEY_PRESSED : KEY_IDLE)}
          >
            ⇧Tab
          </button>

          <div className="w-px bg-neutral-800 shrink-0" />

          {/* Ctrl shortcuts — these bypass sticky modifier (already include Ctrl) */}
          {CTRL_KEYS.map(k => (
            <button
              key={k.key}
              onClick={() => {
                flash(k.key);
                if (CONFIRM_KEYS.has(k.key)) { setConfirmKey(k.key); return; }
                wrappedSendKey(k.key); setCtrlActive(false);
              }}
              className={clsx(KEY_BASE, pressedKey === k.key ? KEY_PRESSED : KEY_IDLE)}
            >
              {k.label}
            </button>
          ))}

          <div className="w-px bg-neutral-800 shrink-0" />

          {/* Arrow keys + special */}
          {SPECIAL_KEYS.map(k => (
            <button
              key={k.key}
              onClick={() => { flash(k.key); wrappedHandleKey(k.key); }}
              className={clsx(KEY_BASE, pressedKey === k.key ? KEY_PRESSED : KEY_IDLE)}
            >
              {k.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
