/**
 * AuthFlow — GM authentication UI.
 *
 * Reads GM's tmux output, shows it live, and provides action buttons
 * based on what state the session is in. No magic, full visibility.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { clsx } from 'clsx';
import { KeyRound, ExternalLink, X, Loader2, MessageSquare, Send } from 'lucide-react';
import {
  getGmAuthState,
  sendGmLogin,
  selectGmOption,
  submitGmAuthCode,
  notifyTelegramAuth,
} from '../lib/api';
import { logAction } from '../lib/user-actions';

interface AuthFlowProps {
  onClose: () => void;
}

type GmState = 'not_running' | 'needs_login' | 'login_options' | 'url_ready' | 'code_prompt' | 'login_success' | 'authenticated' | 'idle' | 'unknown';

export default function AuthFlow({ onClose }: AuthFlowProps) {
  const [gmState, setGmState] = useState<GmState>('unknown');
  const [output, setOutput] = useState('');
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [sending, setSending] = useState(false);
  const [telegramSent, setTelegramSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const outputRef = useRef<HTMLPreElement>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);
  const telegramAttempted = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  /** Poll GM state every 2s */
  const startPolling = useCallback(() => {
    stopPolling();
    const poll = async () => {
      try {
        const data = await getGmAuthState();
        setGmState(data.state as GmState);
        setOutput(data.output || '');
        if (data.url) {
          setAuthUrl(data.url);
          // Auto-send to Telegram once
          if (!telegramAttempted.current) {
            telegramAttempted.current = true;
            try {
              await notifyTelegramAuth(data.url);
              setTelegramSent(true);
              logAction('auth.telegram_sent', 'gm');
            } catch { /* silent */ }
          }
        }
      } catch (err: any) {
        setError(err.message);
      }
    };
    poll();
    pollRef.current = setInterval(poll, 2000);
  }, [stopPolling]);

  useEffect(() => {
    startPolling();
    return stopPolling;
  }, [startPolling, stopPolling]);

  // Auto-scroll output
  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [output]);

  // Focus code input when code prompt appears
  useEffect(() => {
    if ((gmState === 'code_prompt' || gmState === 'url_ready') && codeInputRef.current) {
      codeInputRef.current.focus();
    }
  }, [gmState]);

  /** Send /login to GM */
  const handleLogin = async () => {
    setSending(true);
    setError(null);
    logAction('auth.send_login', 'gm');
    try {
      await sendGmLogin();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  /** Select option 1 (Claude account with subscription) */
  const handleSelectOption = async () => {
    setSending(true);
    setError(null);
    logAction('auth.select_option', 'gm');
    try {
      await selectGmOption();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  /** Submit auth code */
  const handleSubmitCode = async () => {
    if (!code.trim()) return;
    setSending(true);
    setError(null);
    logAction('auth.submit_code', 'gm');
    try {
      await submitGmAuthCode(code.trim());
      setCode('');
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="bg-neutral-900 border border-neutral-700 rounded-xl p-4 space-y-3 max-w-md w-full mx-auto max-h-[85vh] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <KeyRound size={16} className="text-amber-400" />
          <span className="text-sm font-semibold text-neutral-100">GM Auth</span>
          <span className={clsx(
            'text-[10px] px-1.5 py-0.5 rounded font-medium',
            gmState === 'authenticated' || gmState === 'idle' || gmState === 'login_success' ? 'bg-green-500/15 text-green-400' :
            gmState === 'not_running' ? 'bg-red-500/15 text-red-400' :
            'bg-amber-500/15 text-amber-400'
          )}>
            {gmState.replace(/_/g, ' ')}
          </span>
        </div>
        <button onClick={() => { stopPolling(); onClose(); }} className="p-1 text-neutral-500 hover:text-neutral-300">
          <X size={14} />
        </button>
      </div>

      {/* Live GM output */}
      <pre
        ref={outputRef}
        className="bg-black rounded-lg p-2.5 text-[10px] text-green-400/80 font-mono leading-relaxed flex-1 min-h-[120px] max-h-[200px] overflow-y-auto whitespace-pre-wrap border border-neutral-800"
      >
        {output || 'Loading...'}
      </pre>

      {/* Context-aware action buttons */}
      <div className="space-y-2 shrink-0">
        {/* State: needs_login — show Send /login button */}
        {gmState === 'needs_login' && (
          <button
            onClick={handleLogin}
            disabled={sending}
            className="w-full py-2.5 min-h-[44px] rounded-lg bg-amber-500/15 text-amber-400 text-sm font-medium hover:bg-amber-500/25 transition-colors flex items-center justify-center gap-2"
          >
            {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            Send /login
          </button>
        )}

        {/* State: login_options — show Select Account button */}
        {gmState === 'login_options' && (
          <button
            onClick={handleSelectOption}
            disabled={sending}
            className="w-full py-2.5 min-h-[44px] rounded-lg bg-blue-500/15 text-blue-400 text-sm font-medium hover:bg-blue-500/25 transition-colors flex items-center justify-center gap-2"
          >
            {sending ? <Loader2 size={14} className="animate-spin" /> : null}
            Select: Claude account with subscription
          </button>
        )}

        {/* State: login_success — press Enter to continue */}
        {gmState === 'login_success' && (
          <button
            onClick={handleSelectOption}
            disabled={sending}
            className="w-full py-2.5 min-h-[44px] rounded-lg bg-green-500/15 text-green-400 text-sm font-medium hover:bg-green-500/25 transition-colors flex items-center justify-center gap-2"
          >
            {sending ? <Loader2 size={14} className="animate-spin" /> : null}
            Press Enter to continue
          </button>
        )}

        {/* State: url_ready or code_prompt — show URL + code input */}
        {(gmState === 'url_ready' || gmState === 'code_prompt') && (
          <>
            {telegramSent && (
              <div className="flex items-center gap-2 text-xs text-green-400/80">
                <MessageSquare size={12} />
                <span>URL sent to Telegram</span>
              </div>
            )}
            {authUrl && (
              <a
                href={authUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 w-full py-2.5 px-3 min-h-[44px] rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-400 text-sm font-medium hover:bg-blue-500/25 transition-colors"
              >
                <ExternalLink size={14} className="shrink-0" />
                <span className="truncate">Open Login Page</span>
              </a>
            )}
            <div className="flex gap-2">
              <input
                ref={codeInputRef}
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Paste auth code here"
                className="flex-1 bg-neutral-950 border border-neutral-700 rounded-lg px-3 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-blue-500"
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleSubmitCode(); } }}
                disabled={sending}
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck={false}
              />
              <button
                onClick={handleSubmitCode}
                disabled={sending || !code.trim()}
                className={clsx(
                  'px-4 py-2 min-h-[44px] rounded-lg text-sm font-medium transition-colors',
                  sending || !code.trim()
                    ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed'
                    : 'bg-green-500/15 text-green-400 hover:bg-green-500/25'
                )}
              >
                {sending ? <Loader2 size={14} className="animate-spin" /> : 'Submit'}
              </button>
            </div>
          </>
        )}

        {/* State: authenticated or idle */}
        {(gmState === 'authenticated' || gmState === 'idle') && (
          <div className="text-xs text-green-400/80 text-center py-1">
            GM is running. No auth action needed.
          </div>
        )}

        {/* State: not_running */}
        {gmState === 'not_running' && (
          <div className="text-xs text-red-400 text-center py-1">
            GM tmux session not found. Spawn it first.
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <p className="text-[11px] text-red-400 shrink-0">{error}</p>
      )}
    </div>
  );
}
