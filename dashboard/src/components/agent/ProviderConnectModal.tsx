/**
 * ProviderConnectModal — the Connect sheet: what "Connect" on a provider that can't be used yet opens (the model
 * picker closes under it, components/kit/ModelPicker.tsx; this is the top layer).
 *
 * The top says it plainly (lib/providerConnect.ts connectOverview): the two steps a provider needs, "Install Codex"
 * and "Sign in", each done or to do, and the status in words, with "check again" to re-ask. One action does the work:
 * a terminal on this machine, inside the sheet, where you install the CLI and sign in without leaving the page (the
 * sign-in link is lifted out of the terminal's wrapped lines the moment the CLI prints one). The command, the
 * install notes and the browser sign-in alternative are the details, behind "Show details".
 *
 * Kit look: the --bui-* tokens (bg-card, border-line, text-ink), so it reads the same in light and dark.
 */
import { useEffect, useState } from 'react';
import { X, ChevronLeft, ChevronDown, Copy, ExternalLink, Check, Circle, CircleHelp } from 'lucide-react';
import WebTerminal from '../WebTerminal';
import {
  connectOverview, connectPlan, cliFor, installNote, connectSteps, signinCommand,
  type ProviderLike, type StepState,
} from '../../lib/providerConnect';

interface Props {
  provider: ProviderLike | null;
  /** Dismiss the sheet. */
  onClose: () => void;
  /** Back to the providers (the picker reopens): a Back, not only an X that reads as "close everything". */
  onBack?: () => void;
  /** Injected in tests; defaults to the real POST. Returns the opened session name. */
  startLoginShell?: (providerId: string) => Promise<{ ok: boolean; session?: string; error?: string }>;
  /** Re-run the runtime probe (fresh, never the cache) so this sheet can correct itself the moment the CLI is
   *  installed or signed in. */
  onRecheck?: () => Promise<unknown>;
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

/** `backticked` spans render as code, the way they read in a terminal. */
function renderTicks(line: string) {
  return line.split(/(`[^`]+`)/g).map((part, i) =>
    part.startsWith('`') && part.endsWith('`')
      ? <code key={i} className="px-1 py-0.5 rounded bg-field text-ink">{part.slice(1, -1)}</code>
      : <span key={i}>{part}</span>);
}

function StepMark({ state }: { state: StepState }) {
  if (state === 'done') return <span className="flex size-5 items-center justify-center rounded-full bg-green/15 text-green"><Check size={12} strokeWidth={3} /></span>;
  if (state === 'unknown') return <span className="flex size-5 items-center justify-center text-ink-3"><CircleHelp size={16} /></span>;
  return <span className="flex size-5 items-center justify-center text-ink-3"><Circle size={14} /></span>;
}

function CopyLine({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 min-w-0 px-2 py-1.5 rounded-control bg-field text-ink-2 font-mono text-[11.5px] overflow-x-auto whitespace-nowrap">{text}</code>
      <button type="button" aria-label={label} title={copied ? 'Copied' : 'Copy'}
              onClick={() => { void navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); }, () => {}); }}
              className="p-1.5 rounded-control border border-line text-ink-2 hover:bg-hover-2">
        {copied ? <Check size={13} className="text-green" /> : <Copy size={13} />}
      </button>
    </div>
  );
}

export function ProviderConnectModal({ provider, onClose, onBack, startLoginShell = realStartLoginShell, onRecheck }: Props) {
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState<{ provider: string; session: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState(false);
  const [details, setDetails] = useState(false);
  // The CLI prints its sign-in URL hard-wrapped across ~10 terminal lines; the server rejoins it, and this polls for
  // it while the terminal is open, so the moment the CLI prints one there is a real link.
  const [signInUrl, setSignInUrl] = useState<string | null>(null);
  // A terminal belongs to the provider it was opened for: another provider's sheet never shows it.
  const session = opened && provider && opened.provider === provider.id ? opened.session : null;
  useEffect(() => {
    if (!session) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await fetch(`/api/agents/${encodeURIComponent(session)}/sign-in-url`);
        const j = await r.json().catch(() => ({}));
        if (!stop) setSignInUrl(typeof j?.url === 'string' ? j.url : null);
      } catch { /* the pane may not be readable for a moment; keep polling */ }
    };
    void tick();
    const id = setInterval(tick, 2500);
    return () => { stop = true; clearInterval(id); };
  }, [session]);
  // Back from the browser sign-in is a focus: ask again then, so the status catches up by itself.
  useEffect(() => {
    if (!session || !onRecheck) return;
    const onFocus = () => { void onRecheck(); };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [session, onRecheck]);

  if (!provider) return null;

  const o = connectOverview(provider);
  const plan = connectPlan(provider, 'tmux');
  const command = plan.kind === 'login-shell' ? plan.install_command : undefined;
  const note = !provider.installed ? installNote(provider.id) : null;
  const steps = connectSteps(provider.id);
  const cli = cliFor(provider.id);
  const link = session ? signInUrl : null;

  async function openTerminal() {
    if (busy || !provider) return;
    setBusy(true); setError(null);
    const r = await startLoginShell(provider.id);
    setBusy(false);
    if (r.ok && r.session) setOpened({ provider: provider.id, session: r.session }); else setError(r.error || "Couldn't open a terminal");
  }

  // The backdrop is the least destructive exit: back to the providers when there is somewhere to go back to.
  const dismiss = onBack || onClose;

  return (
    <div className="fixed inset-0 z-[200] flex items-end justify-center bg-black/50" onClick={dismiss} data-connect-sheet>
      <div className="w-full max-w-lg h-[94vh] min-h-0 flex flex-col overflow-hidden rounded-t-2xl bg-card text-ink border-t border-line p-4 pb-6"
           role="dialog" aria-label={`Connect ${provider.label}`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-3">
          {onBack && (
            <button onClick={onBack} aria-label="Back to providers" data-testid="connect-back"
                    className="-ml-1 p-1 rounded-control hover:bg-hover-2 text-ink-2 hover:text-ink flex items-center gap-1">
              <ChevronLeft size={18} /><span className="text-xs">Providers</span>
            </button>
          )}
          <h2 className="text-[15px] font-semibold flex-1 truncate">Connect {provider.label}</h2>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded-control hover:bg-hover-2 text-ink-2"><X size={18} /></button>
        </div>

        {/* The plain top: the steps, and where it stands. */}
        <ol className="rounded-lg border border-line divide-y divide-line mb-2" data-testid="connect-steps-plain">
          {o.steps.map((s) => (
            <li key={s.label} className="flex items-center gap-3 px-3 py-2.5 text-[13.5px]">
              <StepMark state={s.state} />
              <span className={`flex-1 ${s.state === 'done' ? 'text-ink-2' : 'text-ink font-medium'}`}>{s.label}</span>
              <span className="text-[12px] text-ink-3">{s.state === 'done' ? 'Done' : s.state === 'unknown' ? 'Not checked' : 'To do'}</span>
            </li>
          ))}
        </ol>
        <p className={`text-[12.5px] mb-3 flex items-center gap-2 ${o.connected ? 'text-green' : 'text-ink-2'}`} data-testid="connect-reason" role="status">
          <span>Status: <span className={o.connected ? '' : 'text-red font-medium'}>{o.status}</span></span>
          {onRecheck && (
            <button className="underline underline-offset-2 text-ink-3 hover:text-ink" disabled={rechecking}
                    onClick={async () => { setRechecking(true); await onRecheck(); setRechecking(false); }}>
              {rechecking ? 'checking…' : 'check again'}
            </button>
          )}
        </p>

        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-2 text-[12.5px] text-ink-2 leading-relaxed">
          {!o.connected && !session && (
            <button onClick={() => void openTerminal()} disabled={busy} data-testid="connect-open-terminal"
                    className="self-start px-3.5 py-2 rounded-full bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50">
              {busy ? 'Opening…' : provider.installed ? `Sign in to ${provider.label}` : `Install ${provider.label}`}
            </button>
          )}
          {!o.connected && !session && (
            <p className="text-ink-3">Opens a terminal on this machine, here in this sheet{provider.installed ? `, running ${cli} so you can sign in.` : '. Install it there, then sign in.'}</p>
          )}
          {link && (
            <div className="flex items-center gap-2" data-testid="signin-link">
              <a href={link} target="_blank" rel="noreferrer"
                 className="flex-1 flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[#d97757] text-tone-white font-medium">
                <ExternalLink size={13} /> Open the sign-in page
              </a>
              <button aria-label="Copy the sign-in link" title="Copy the link" onClick={() => { void navigator.clipboard?.writeText(link); }}
                      className="p-2 rounded-lg border border-line hover:bg-hover-2"><Copy size={13} /></button>
            </div>
          )}
          {link && <p className="text-ink-3">Sign in there, then paste the code it gives you back into the terminal below.</p>}
          {error && <p className="text-red">Couldn't open a terminal: {error}</p>}

          {/* The technical part, one tap away. */}
          <button type="button" onClick={() => setDetails((d) => !d)} aria-expanded={details} data-testid="connect-details-toggle"
                  className="self-start flex items-center gap-1 text-ink-3 hover:text-ink">
            <ChevronDown size={13} className="transition-transform" style={{ transform: details ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
            {details ? 'Hide details' : 'Show details'}
          </button>
          {details && (
            <div className="flex flex-col gap-2 rounded-lg border border-line p-3" data-testid="connect-details">
              {command && <><p>Install command (paste it into the terminal):</p><CopyLine text={command} label="Copy install command" /></>}
              {note && <p data-testid="install-note">{note}</p>}
              {steps.length > 0 && (
                <ol className="flex flex-col gap-1.5 pl-4 list-decimal text-ink-3" data-testid="connect-steps">
                  {steps.map((line, i) => <li key={i}>{renderTicks(line)}</li>)}
                </ol>
              )}
              {provider.installed && (
                <>
                  <p>Or sign in from a terminal of your own; the CLI opens your browser:</p>
                  <CopyLine text={signinCommand(provider.id)} label="Copy sign-in command" />
                </>
              )}
            </div>
          )}

          {session && (
            // The live login cursor stays pinned in the visible sheet: the notes above scroll, the terminal does not.
            <div className="sticky bottom-0 shrink-0 h-[min(62vh,calc(94vh-12rem))] min-h-[220px] rounded-lg overflow-hidden border border-line bg-background"
                 data-testid="connect-terminal">
              <WebTerminal session={session} machine="vps" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
