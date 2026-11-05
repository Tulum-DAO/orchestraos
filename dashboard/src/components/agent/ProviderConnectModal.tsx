/**
 * ProviderConnectModal — the Connect sheet: what "Install <Provider>" / "Sign in" on a provider that can't be used yet
 * opens (the model picker closes under it, components/kit/ModelPicker.tsx; this is the top layer).
 *
 * No terminal in the normal path. For Codex, Grok and Gemini the server does the work (api/src/routes/
 * provider-connect.ts): it checks the box, installs the CLI if it is missing, starts the sign-in and reads the link and
 * code off the CLI's screen. The sheet shows a short step list, then the code with Copy, "Open sign-in page" and a
 * countdown where the CLI gives one, or (Gemini) a box for the code Google shows. It polls while open and flips to
 * Signed in by itself. Closing it does not stop anything: reopening resumes the same attempt. The raw output is behind
 * Details. Claude, which the server does not handle, keeps the terminal.
 *
 * Size ("a bit too tall"): content height, never a fixed one. Capped at min(85dvh, 560px) with the
 * body scrolling only past that; a centred card on a wide screen, a bottom sheet sized to its content on a phone.
 *
 * A real dialog: aria-modal, Esc closes it, focus starts on the main action and stays inside (Tab wraps), and the phone
 * sheet keeps clear of the home indicator (safe-area padding).
 *
 * Kit look: the --bui-* tokens (bg-card, border-line, text-ink), so it reads the same in light and dark.
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { X, ChevronLeft, ChevronDown, Copy, ExternalLink, Check, Circle, Loader2, AlertCircle } from 'lucide-react';
import WebTerminal from '../WebTerminal';
import { signinCommand, installCommand, type ProviderLike } from '../../lib/providerConnect';
import { useInstallAdmin } from '../../lib/myRole';
import {
  sheetView, countdown, isActivePhase, isManagedProvider, looksLikeGoogleCode, connectApi, anchorExpiry, codeSubmitWords,
  sheetKeyAction, copyWithTextarea,
  type ConnectApi, type ConnectState, type StepState,
} from '../../lib/providerConnectFlow';

interface Props {
  provider: ProviderLike | null;
  /** Dismiss the sheet. */
  onClose: () => void;
  /** Back to the providers (the picker reopens): a Back, not only an X that reads as "close everything". */
  onBack?: () => void;
  /** Re-run the runtime probe (fresh, never the cache): called once when the sign-in completes. */
  onRecheck?: () => Promise<unknown>;
  /** Injected in tests; defaults to the real API. */
  api?: ConnectApi;
  /** Claude only (not server-run): open a terminal. Injected in tests; defaults to the real POST. */
  startLoginShell?: (providerId: string) => Promise<{ ok: boolean; session?: string; error?: string }>;
}

const POLL_MS = 2000;

async function realStartLoginShell(providerId: string) {
  try {
    const res = await fetch('/api/agents/login-shell', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: providerId }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || !json.ok) return { ok: false, error: json.reason || json.error || `HTTP ${res.status}` };
    return { ok: true, session: json.session as string };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'network' };
  }
}

function StepMark({ state }: { state: StepState }) {
  if (state === 'done') return <Check size={14} strokeWidth={3} className="text-green" />;
  if (state === 'active') return <Loader2 size={14} className="animate-spin text-ink-2" />;
  if (state === 'failed') return <AlertCircle size={14} className="text-red" />;
  return <Circle size={12} className="text-ink-3" />;
}

/** Copy: the Clipboard API on a secure context, else the textarea fallback (plain http over Tailscale has no
 *  navigator.clipboard). Focus goes back to `restore` either way. False when neither copied. */
async function copyText(text: string, restore: HTMLElement | null): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  }
  return copyWithTextarea(document as unknown as Parameters<typeof copyWithTextarea>[0], text, restore);
}

const COPY_KEY = /Mac|iPhone|iPad/.test(typeof navigator === 'undefined' ? '' : navigator.platform) ? 'Cmd+C' : 'Ctrl+C';

/** Copy, and when the browser won't, select the text so a key press copies it, and say which key. */
function CopyButton({ text, label, selectRef }: { text: string; label: string; selectRef: RefObject<HTMLElement | null> }) {
  const [state, setState] = useState<'idle' | 'copied' | 'manual'>('idle');
  return (
    <span className="shrink-0 flex items-center gap-2">
      {state === 'manual' && <span className="text-px11h text-ink-3" role="status">Press {COPY_KEY}</span>}
      <button type="button" aria-label={label} title={state === 'copied' ? 'Copied' : 'Copy'} data-testid="connect-copy"
              onClick={(e) => { const btn = e.currentTarget; void copyText(text, btn).then((ok) => {
                if (ok) { setState('copied'); setTimeout(() => setState('idle'), 1200); return; }
                const el = selectRef.current;
                if (el) window.getSelection()?.selectAllChildren(el);
                setState('manual');
              }); }}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-control border border-line text-ink-2 hover:bg-hover-2 text-px12">
        {state === 'copied' ? <Check size={13} className="text-green" /> : <Copy size={13} />}{state === 'copied' ? 'Copied' : 'Copy'}
      </button>
    </span>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ProviderConnectModal({ provider, onClose, onBack, onRecheck, api = connectApi, startLoginShell = realStartLoginShell }: Props) {
  const admin = useInstallAdmin();
  const managed = !!provider && isManagedProvider(provider.id);
  const [state, setState] = useState<ConnectState | null>(null);
  // This browser's clock: set with each answer (so the countdown is right from its first frame) and every second.
  const [now, setNow] = useState(() => Date.now());
  const [member, setMember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [codeHint, setCodeHint] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const [output, setOutput] = useState('');
  const [terminal, setTerminal] = useState<string | null>(null);
  const recheckedFor = useRef<number | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const codeRef = useRef<HTMLSpanElement>(null);
  const providerId = provider?.id ?? null;

  const apply = useCallback((r: Awaited<ReturnType<ConnectApi['get']>>) => {
    if (r.ok) {
      const at = Date.now();
      setState(anchorExpiry(r.state, at)); setNow(at); setRequestError(null); setMember(false);
    }
    else if (r.member) setMember(true);
    else setRequestError(r.error);
  }, []);

  // Read the attempt when the sheet opens (it may already be running: same code, same timer), then poll while live.
  const phase = state?.provider === providerId ? state?.phase ?? null : null;
  const live = !!phase && isActivePhase(phase);
  useEffect(() => {
    if (!providerId || !managed || !admin) return;
    let stop = false;
    const tick = async () => { const r = await api.get(providerId); if (!stop) apply(r); };
    void tick();
    if (!live) return () => { stop = true; };
    const id = setInterval(tick, POLL_MS);
    return () => { stop = true; clearInterval(id); };
  }, [providerId, managed, admin, live, api, apply]);

  // Signed in: the picker re-probes once, so the provider turns selectable behind the sheet.
  useEffect(() => {
    if (phase !== 'signed_in' || !state?.attemptId || recheckedFor.current === state.attemptId) return;
    recheckedFor.current = state.attemptId;
    void onRecheck?.();
  }, [phase, state?.attemptId, onRecheck]);

  // Details: the raw output, refreshed while it is open.
  useEffect(() => {
    if (!details || !providerId || !managed) return;
    let stop = false;
    const tick = async () => { const o = await api.output(providerId); if (!stop) setOutput(o); };
    void tick();
    const id = setInterval(tick, 3000);
    return () => { stop = true; clearInterval(id); };
  }, [details, providerId, managed, api]);

  const counting = !!state?.expiresAt && phase === 'waiting';
  useEffect(() => {
    if (!counting) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [counting]);

  // A modal: Esc closes it; Tab and Shift+Tab stay inside it. Listened for on the sheet itself, bubbling: the Claude
  // terminal (xterm) inside gets Esc and Tab first and keeps them (sheetKeyAction), and nothing pressed in the sheet
  // reaches an outer handler such as ArturoPanel's window-level Tab trap, which would pull focus back under the sheet.
  useEffect(() => {
    const box = dialogRef.current;
    if (!providerId || !box) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target instanceof Element ? e.target : null;
      const items = [...box.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
      const act = sheetKeyAction({
        key: e.key, shiftKey: e.shiftKey,
        inTerminal: !!target?.closest('.xterm, [data-testid="connect-terminal"]'),
        atFirst: document.activeElement === items[0] || document.activeElement === box,
        atLast: document.activeElement === items[items.length - 1],
        count: items.length,
      });
      if (act.stop) e.stopPropagation();
      if (act.prevent) e.preventDefault();
      if (act.action === 'close') onClose();
      else if (act.action === 'focus-first') items[0]?.focus();
      else if (act.action === 'focus-last') items[items.length - 1]?.focus();
      else if (act.action === 'focus-box') box.focus();
    };
    box.addEventListener('keydown', onKey);
    return () => box.removeEventListener('keydown', onKey);
  }, [providerId, onClose]);

  // Focus starts on the main action (or the dialog itself), and goes there again when the main action changes.
  const mainKey = `${providerId}:${phase ?? 'none'}`;
  useEffect(() => {
    if (!providerId) return;
    const t = setTimeout(() => {
      const box = dialogRef.current;
      if (!box || (document.activeElement && box.contains(document.activeElement) && document.activeElement !== box)) return;
      (box.querySelector<HTMLElement>('[data-autofocus]') ?? box).focus();
    }, 0);
    return () => clearTimeout(t);
  }, [mainKey, providerId]);

  if (!provider) return null;
  const shown = state?.provider === provider.id ? state : null;
  const v = sheetView(shown, provider, admin && !member);
  const left = countdown(v.expiresAt, now);

  async function run(action: 'start' | 'retry' | 'new_code') {
    if (busy || !provider) return;
    setBusy(true);
    apply(await api.start(provider.id, action !== 'start'));
    setBusy(false);
  }
  async function cancel() {
    if (!provider) return;
    setBusy(true);
    const r = await api.cancel(provider.id);
    if (r.ok) { setState(null); setCode(''); setCodeHint(null); }
    else apply(await api.get(provider.id));     // refused while installing: show where it stands
    setBusy(false);
  }
  async function sendCode() {
    if (!provider || busy) return;
    if (!looksLikeGoogleCode(code)) { setCodeHint(codeSubmitWords('bad_code')); return; }
    setBusy(true); setCodeHint(null);
    const r = await api.code(provider.id, code);
    // Refused (not ready yet, bad charset): say so and KEEP what was typed.
    if (r.ok) setCode('');
    else if (!r.member) setCodeHint(codeSubmitWords(r.error));
    apply(r.ok ? r : await api.get(provider.id));
    setBusy(false);
  }
  async function openTerminal() {
    if (!provider || busy) return;
    setBusy(true);
    const r = await startLoginShell(provider.id);
    setBusy(false);
    if (r.ok && r.session) setTerminal(r.session); else setRequestError(r.error || "Couldn't open a terminal");
  }

  // The backdrop is the least destructive exit: back to the providers when there is somewhere to go back to.
  const dismiss = onBack || onClose;
  const btn = 'px-3.5 py-2 rounded-full font-medium disabled:opacity-50';

  return (
    <div className="fixed inset-0 z-[200] flex items-end sm:items-center justify-center bg-black/50 sm:p-4" onClick={dismiss} data-connect-sheet>
      <div ref={dialogRef} tabIndex={-1}
           className="w-full sm:max-w-md max-h-[min(85dvh,560px)] flex flex-col overflow-hidden rounded-t-2xl sm:rounded-2xl bg-card text-ink border border-line shadow-xl outline-none"
           role="dialog" aria-modal="true" aria-label={`Connect ${provider.label}`} onClick={(e) => e.stopPropagation()} data-testid="connect-dialog">
        <div className="shrink-0 flex items-center gap-2 px-4 pt-3 pb-2">
          {onBack && (
            <button onClick={onBack} aria-label="Back to providers" data-testid="connect-back"
                    className="-ml-1 p-1 rounded-control hover:bg-hover-2 text-ink-2 hover:text-ink flex items-center gap-1">
              <ChevronLeft size={18} /><span className="text-xs">Providers</span>
            </button>
          )}
          <h2 className="text-px15 font-semibold flex-1 truncate">
            {v.kind === 'done' ? `${provider.label}: signed in` : `Connect ${provider.label}`}
          </h2>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded-control hover:bg-hover-2 text-ink-2"><X size={18} /></button>
        </div>

        <div className="min-h-0 overflow-y-auto px-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:pb-4 flex flex-col gap-3 text-px13h text-ink-2 leading-relaxed" data-testid="connect-body">
          {!managed ? (
            // Claude: not run by the server; its own sign-in, in a terminal here.
            <>
              {/* The server does not install this one: the terminal is where it is done, and the sheet says so. */}
              {!provider.installed && installCommand(provider.id) && (
                <p>Install it in a terminal on this machine: <code className="px-1 rounded bg-field text-ink break-all">{installCommand(provider.id)}</code></p>
              )}
              <p>{provider.installed ? 'Sign in from a terminal on this machine:' : 'Then sign in:'} <code className="px-1 rounded bg-field text-ink">{signinCommand(provider.id).replace(/\s+/g, ' ')}</code></p>
              {!terminal && (
                <button onClick={() => void openTerminal()} disabled={busy || !admin} className={`self-start ${btn} bg-foreground text-background`} data-testid="connect-open-terminal" data-autofocus>
                  {busy ? 'Opening…' : 'Open a terminal'}
                </button>
              )}
              {!admin && <p className="text-ink-3">Only an admin can open a terminal on this machine.</p>}
              {requestError && <p className="text-red" role="alert">Couldn't open a terminal: {requestError}</p>}
              {terminal && (
                <div className="h-72 rounded-lg overflow-hidden border border-line bg-background" data-testid="connect-terminal">
                  <WebTerminal session={terminal} machine="vps" />
                </div>
              )}
            </>
          ) : v.kind === 'member' ? (
            <p data-testid="connect-member">Only an admin can install or sign in to providers on this machine. Ask an admin to connect {provider.label}.</p>
          ) : (
            <>
              {v.kind === 'done' && (
                <p className="flex items-center gap-2 text-green font-medium" role="status" data-testid="connect-done">
                  <Check size={16} strokeWidth={3} /> Signed in. {provider.label} is ready in the model picker.
                </p>
              )}

              {v.steps.length > 0 && v.kind !== 'done' && (
                <ol className="flex flex-col gap-1.5" data-testid="connect-steps">
                  {v.steps.map((s) => (
                    <li key={s.label} className="flex items-center gap-2.5">
                      <span className="flex size-4 items-center justify-center"><StepMark state={s.state} /></span>
                      <span className={s.state === 'active' ? 'text-ink font-medium' : s.state === 'failed' ? 'text-red' : ''}>
                        {s.state === 'active' && v.status ? v.status : s.label}
                      </span>
                    </li>
                  ))}
                </ol>
              )}

              {v.notice && <p className="text-ink" role="status" data-testid="connect-notice">{v.notice}</p>}

              {v.prerequisite && (
                <p className="text-px12h" data-testid="connect-prereq">
                  {v.prerequisite.text}{' '}
                  <a href={v.prerequisite.href} target="_blank" rel="noreferrer" className="underline underline-offset-2 text-ink hover:opacity-80">{v.prerequisite.link}</a>
                </p>
              )}

              {v.code && (
                <div className="flex items-center gap-2 rounded-lg border border-line px-3 py-2" data-testid="connect-code">
                  <span ref={codeRef} className="flex-1 font-mono text-lg font-semibold tracking-wider text-ink select-all">{v.code}</span>
                  <CopyButton text={v.code} label="Copy the code" selectRef={codeRef} />
                </div>
              )}
              {v.code && left && <p className="text-px12 text-ink-3 -mt-1.5" data-testid="connect-countdown">Code expires in {left}</p>}

              {v.url && (
                <a href={v.url} target="_blank" rel="noreferrer" data-testid="signin-link" data-autofocus
                   className="self-start flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-[#d97757] text-tone-white font-medium">
                  <ExternalLink size={13} /> Open sign-in page
                </a>
              )}
              {v.url && v.code && <p className="text-px12h text-ink-3 -mt-1">Enter the code there and approve. This sheet updates by itself.</p>}

              {v.pasteBox && (
                <form className="flex flex-col gap-1.5" onSubmit={(e) => { e.preventDefault(); void sendCode(); }} data-testid="connect-paste">
                  <label htmlFor="connect-code-input" className="text-px12h">Paste the code Google shows you</label>
                  <div className="flex gap-2">
                    <input id="connect-code-input" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" spellCheck={false}
                           disabled={phase === 'checking_code'}
                           className="flex-1 min-w-0 px-2.5 py-1.5 rounded-control border border-line bg-field text-ink font-mono text-px12" />
                    <button type="submit" disabled={busy || !code.trim() || phase === 'checking_code'} className={`${btn} bg-foreground text-background`}>
                      {phase === 'checking_code' ? 'Checking…' : 'Submit'}
                    </button>
                  </div>
                  {(codeHint || v.codeError) && <p className="text-red text-px12h" data-testid="connect-code-error">{codeHint || v.codeError}</p>}
                </form>
              )}

              {v.error && <p className="text-red" role="alert" data-testid="connect-error">{v.error}</p>}
              {requestError && <p className="text-red" role="alert">Couldn't reach the server: {requestError}</p>}

              {(v.primary || v.cancellable || v.kind === 'done') && (
                <div className="flex items-center gap-3">
                  {v.primary && (
                    <button onClick={() => void run(v.primary!.action)} disabled={busy} data-testid="connect-primary" data-autofocus
                            className={`${btn} bg-foreground text-background`}>
                      {busy && v.primary.action === 'start' ? 'Starting…' : v.primary.label}
                    </button>
                  )}
                  {v.kind === 'done' && (
                    <button onClick={dismiss} className={`${btn} bg-foreground text-background`} data-testid="connect-finish" data-autofocus>Done</button>
                  )}
                  {v.cancellable && (
                    <button onClick={() => void cancel()} disabled={busy} className="text-px12h text-ink-3 hover:text-ink underline underline-offset-2" data-testid="connect-cancel">
                      Cancel
                    </button>
                  )}
                </div>
              )}

              {shown && shown.phase !== 'idle' && (
                <div className="flex flex-col gap-1.5">
                  <button type="button" onClick={() => setDetails((d) => !d)} aria-expanded={details} data-testid="connect-details-toggle"
                          className="self-start flex items-center gap-1 text-px12 text-ink-3 hover:text-ink">
                    <ChevronDown size={13} className="transition-transform" style={{ transform: details ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
                    Details
                  </button>
                  {details && (
                    <pre className="max-h-40 overflow-auto rounded-lg border border-line bg-background p-2 font-mono text-px11h text-ink-2 whitespace-pre-wrap break-all" data-testid="connect-details">
                      {output.trim() || 'No output yet.'}
                    </pre>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
