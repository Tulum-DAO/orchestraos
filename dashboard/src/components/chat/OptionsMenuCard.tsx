/**
 * OptionsMenuCard — renders a live decision menu (detector `pending_menu`) at
 * the bottom of TranscriptChatView and answers it via the two-phase /agent-key
 * route. Detector-served (NOT pane-scraped); iOS build 69-72 is the behavioral
 * reference (arm → expand → confirm).
 *
 * Interaction (spec C2b):
 *  - First tap on an option EXPANDS it in-div (full label + detail subline) and
 *    ARMS it (client-side); tapping a different option swaps the expansion.
 *  - Second tap on the armed option SENDS (POST /agent-key confirm:true).
 *  - 30s auto-disarm; spinner while sending; blue accent throughout.
 *  - selected_n (the agent's ❯ lean) shows a labeled "agent leans" chip.
 *  - 409 (menu vanished) → disarm + "answered elsewhere"; 403 → verbatim notice.
 *
 * The menu object arrives as a prop from the agents poll (≤10s stale), but the
 * gateway re-checks the LIVE detector on confirm, so a stale render can only
 * fail closed (409), never mis-send.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { Loader2 } from 'lucide-react';
import { sendAgentKey } from '../../lib/api';

export interface PendingMenuOption { n: string; label: string; detail?: string }
export interface PendingMenu {
  kind: 'options' | 'permission' | 'yes_no';
  question: string;
  options: PendingMenuOption[];
  selected_n?: string | null;
  chrome?: string;
  captured_at?: number;
}

const ARM_TIMEOUT_MS = 30_000;

export default function OptionsMenuCard({ agentId, menu }: { agentId: string; menu: PendingMenu }) {
  const [armed, setArmed] = useState<string | null>(null);   // option n, expanded + armed
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null); // refusal / done text
  const [done, setDone] = useState<string | null>(null);      // sent digit → success latch
  const disarmTimer = useRef<number | null>(null);

  // A new menu (different question / capture) resets any stale arm state.
  const menuKey = useMemo(
    () => `${menu.question}|${menu.options.map((o) => o.n + o.label).join('|')}`,
    [menu]
  );
  useEffect(() => {
    setArmed(null); setSending(false); setNotice(null); setDone(null);
  }, [menuKey]);

  const clearDisarm = () => {
    if (disarmTimer.current) { clearTimeout(disarmTimer.current); disarmTimer.current = null; }
  };
  useEffect(() => clearDisarm, []);

  const arm = (n: string) => {
    if (sending || done) return;
    setNotice(null);
    setArmed(n);
    clearDisarm();
    disarmTimer.current = window.setTimeout(() => setArmed(null), ARM_TIMEOUT_MS);
  };

  const confirm = async (n: string) => {
    if (sending) return;
    clearDisarm();
    setSending(true);
    setNotice(null);
    try {
      const r = await sendAgentKey(agentId, n, true);
      if (r.status === 200 && r.ok) {
        setDone(n);
        setNotice(null);
      } else if (r.status === 409) {
        setArmed(null);
        setNotice('Menu is gone — answered elsewhere.');
      } else if (r.status === 403) {
        setArmed(null);
        setNotice(r.error || 'That key is not enabled yet.');
      } else if (r.status === 428) {
        // Shouldn't happen (we send confirm:true), but handle defensively.
        setNotice(r.confirm_text || 'Confirmation required — tap again.');
      } else {
        setArmed(null);
        setNotice(r.error || 'Could not send — try again.');
      }
    } catch (e: any) {
      setArmed(null);
      setNotice('Network error — try again.');
    } finally {
      setSending(false);
    }
  };

  const onTap = (n: string) => {
    if (done) return;
    if (armed === n) confirm(n);
    else arm(n);
  };

  const lean = menu.selected_n && menu.options.some((o) => o.n === menu.selected_n) ? menu.selected_n : null;
  const isPermission = menu.kind === 'permission';

  return (
    <div className={clsx(
      'rounded-lg border px-3 py-2.5 mt-1',
      isPermission ? 'border-amber-600/40 bg-amber-500/5' : 'border-blue-600/40 bg-blue-500/5'
    )}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className={clsx('text-[10px] uppercase tracking-wide font-medium',
          isPermission ? 'text-amber-300' : 'text-blue-300')}>
          {isPermission ? 'Permission' : 'Decision needed'}
        </span>
        {lean && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">
            agent leans {lean}
          </span>
        )}
      </div>

      {menu.question && (
        <div className="text-xs text-neutral-300 mb-2 whitespace-pre-wrap">{menu.question}</div>
      )}

      <div className="space-y-1">
        {menu.options.map((opt) => {
          const isArmed = armed === opt.n;
          const isDone = done === opt.n;
          const dimmed = (done && !isDone) || (sending && !isArmed);
          return (
            <button
              key={opt.n}
              onClick={() => onTap(opt.n)}
              disabled={!!done || sending}
              className={clsx(
                'w-full text-left rounded-md px-2.5 py-1.5 transition-colors flex items-start gap-2',
                isDone
                  ? 'bg-green-600/15 ring-1 ring-green-500/40'
                  : isArmed
                    ? 'bg-blue-600/20 ring-1 ring-blue-500/50'
                    : dimmed
                      ? 'bg-neutral-800/40 text-neutral-600 cursor-not-allowed'
                      : 'bg-neutral-800 hover:bg-neutral-700'
              )}
            >
              <span className={clsx('font-mono text-[11px] mt-0.5 shrink-0',
                isArmed ? 'text-blue-300' : isDone ? 'text-green-400' : 'text-neutral-500')}>
                {opt.n}.
              </span>
              <span className="min-w-0 flex-1">
                <span className={clsx('text-xs block',
                  isArmed || isDone ? 'text-neutral-100' : 'text-neutral-300',
                  // collapse label to one line unless armed/expanded
                  !isArmed && !isDone && 'truncate'
                )}>
                  {opt.label}
                </span>
                {/* detail subline only when expanded (armed) */}
                {isArmed && opt.detail && (
                  <span className="text-[11px] text-neutral-400 block mt-0.5">{opt.detail}</span>
                )}
              </span>
              {isArmed && sending && <Loader2 size={13} className="animate-spin text-blue-300 mt-0.5 shrink-0" />}
              {isArmed && !sending && (
                <span className="text-[10px] text-blue-300 mt-0.5 shrink-0 whitespace-nowrap">tap to send ✓</span>
              )}
              {isDone && <span className="text-[10px] text-green-400 mt-0.5 shrink-0">sent</span>}
            </button>
          );
        })}
      </div>

      {notice && <div className="text-[11px] text-amber-400/90 mt-1.5">{notice}</div>}
      {armed && !sending && !done && (
        <div className="text-[10px] text-neutral-500 mt-1.5">
          Armed option {armed} — tap again to confirm (auto-cancels in 30s).
        </div>
      )}
    </div>
  );
}
