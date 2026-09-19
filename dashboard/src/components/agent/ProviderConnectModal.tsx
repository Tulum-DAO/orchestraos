/**
 * ProviderConnectModal — what a DISCONNECTED provider tile opens.
 *
 * Operator, 2026-09-18: clicking a disconnected provider shows a modal with a toggle at the
 * top between TMUX and OAUTH. Those are the two honest ways to connect one (see
 * lib/providerConnect.ts for why OAUTH names a command rather than minting its own URL).
 *
 * It also closes a real gap rather than only adding a feature: before this, a red tile was
 * DISABLED, and the reason it was red reached the operator through the tile's `title`
 * attribute — which iOS Safari does not reliably surface on long-press. On a phone the
 * reason was effectively unreachable. Here it is stated in words, on the surface.
 */
import { useEffect, useState } from 'react';
import { X, Terminal, Globe, Copy } from 'lucide-react';
import WebTerminal from '../WebTerminal';
import {
  connectModes, defaultMode, connectPlan, reasonText, cliFor, installNote,
  type ProviderLike, type ModeId,
} from '../../lib/providerConnect';

interface Props {
  provider: ProviderLike | null;
  onClose: () => void;
  /** Injected in tests; defaults to the real POST. Returns the opened session name. */
  startLoginShell?: (providerId: string) => Promise<{ ok: boolean; session?: string; error?: string }>;
}

async function realStartLoginShell(providerId: string) {
  try {
    const res = await fetch('/api/agents/login-shell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: providerId }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || !json.ok) return { ok: false, error: json.reason || json.error || `HTTP ${res.status}` };
    return { ok: true, session: json.session as string };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

export function ProviderConnectModal({ provider, onClose, startLoginShell = realStartLoginShell }: Props) {
  const [mode, setMode] = useState<ModeId>(provider ? defaultMode(provider) : 'tmux');
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Switching provider inside the sheet must not keep the PREVIOUS provider's terminal.
  // The operator hit this: the dialog said "Connect Codex" while the pane below it was the
  // agy session he had opened a moment earlier — a shell attributed to the wrong provider.
  useEffect(() => {
    setOpened(null);
    setError(null);
    setMode(provider ? defaultMode(provider) : 'tmux');
  }, [provider?.id]);

  if (!provider) return null;

  const modes = connectModes(provider);
  const plan = connectPlan(provider, mode);
  const reason = reasonText(provider);

  async function openTerminal() {
    if (busy || !provider) return;
    setBusy(true); setError(null);
    const r = await startLoginShell(provider.id);
    setBusy(false);
    if (r.ok) setOpened(r.session || null); else setError(r.error || 'could not open a terminal');
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/50" onClick={onClose}>
      <div className="w-full max-w-lg rounded-t-2xl bg-background border-t border-border p-4 pb-6"
           role="dialog" aria-label={`Connect ${provider.label}`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-sm font-medium text-foreground">Connect {provider.label}</h2>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded hover:bg-muted"><X size={18} /></button>
        </div>

        {/* The reason, in words. This is the half a phone could not reach before. */}
        {reason && <p className="text-xs text-red-400/90 mb-3" data-testid="connect-reason">{provider.label} is not connected: {reason}</p>}

        {/* The toggle the operator asked for, at the top. */}
        <div className="flex gap-1 p-1 rounded-lg bg-muted/60 mb-3" role="tablist" aria-label="How to connect">
          {modes.map((m) => (
            <button key={m.id} role="tab" aria-selected={mode === m.id}
                    onClick={() => setMode(m.id)}
                    className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition-colors ${
                      mode === m.id ? 'bg-background text-foreground' : 'text-foreground/55 hover:text-foreground/80'
                    }`}>
              {m.id === 'tmux' ? <Terminal size={13} /> : <Globe size={13} />}
              {m.label}
            </button>
          ))}
        </div>

        {(() => {
          const m = modes.find((x) => x.id === mode)!;
          if (!m.available) {
            return (
              <div className="text-xs text-foreground/70 leading-relaxed" data-testid="mode-blocked">
                <p className="mb-2">{m.unavailable_reason}</p>
                <p className="text-foreground/45">Install <code>{cliFor(provider.id)}</code> on this machine, then reopen this sheet — it re-probes every time it opens.</p>
              </div>
            );
          }
          if (mode === 'tmux') {
            const cmd = plan.kind === 'login-shell' ? plan.install_command : undefined;
            return (
              <div className="text-xs text-foreground/70 leading-relaxed flex flex-col gap-2" data-testid="mode-tmux">
                <p>{m.blurb}</p>
                {/* On a blank machine this command is the whole job, so it is offered where
                    it is RUN — one tap to copy, then paste into the terminal below. */}
                {!cmd && !provider.installed && installNote(provider.id) && (
                  <p className="text-foreground/80" data-testid="install-note">{installNote(provider.id)}</p>
                )}
                {cmd && (
                  <div className="flex items-center gap-2">
                    <code className="flex-1 px-2 py-1.5 rounded bg-muted text-foreground/90 overflow-x-auto">{cmd}</code>
                    <button aria-label="Copy install command" title="Copy"
                            onClick={() => { void navigator.clipboard?.writeText(cmd); }}
                            className="p-1.5 rounded border border-border hover:bg-muted"><Copy size={13} /></button>
                  </div>
                )}
                {!opened && (
                  <button onClick={() => void openTerminal()} disabled={busy}
                          className="self-start px-3 py-1.5 rounded-lg border border-border text-foreground hover:bg-muted disabled:opacity-50">
                    {busy ? 'Opening…' : cmd ? 'Open a terminal here' : 'Open a terminal'}
                  </button>
                )}
                {/* The terminal lives INSIDE the modal: a blank machine installs the CLI and
                    signs in without ever leaving this window. */}
                {opened && (
                  <div className="rounded-lg overflow-hidden border border-border" data-testid="connect-terminal">
                    <WebTerminal session={opened} machine="vps" />
                  </div>
                )}
                {error && <p className="text-red-400/90">Could not open a terminal: {error}</p>}
                <p className="text-foreground/45">{plan.kind === 'login-shell' ? plan.detail : ''}</p>
              </div>
            );
          }
          return (
            <div className="text-xs text-foreground/70 leading-relaxed flex flex-col gap-2" data-testid="mode-oauth">
              <p>{m.blurb}</p>
              <code className="px-2 py-1.5 rounded bg-muted text-foreground/90">{plan.kind === 'browser-signin' ? plan.command : ''}</code>
              <p className="text-foreground/45">{plan.kind === 'browser-signin' ? plan.detail : ''}</p>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
