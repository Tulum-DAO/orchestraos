import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Bell, ShieldCheck, ClipboardList, X } from 'lucide-react';
import { getGmAuthState } from '../lib/api';
import AuthFlow from './AuthFlow';
import { isViewHidden } from '../lib/features';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const NEEDS_AUTH_STATES = new Set([
  'needs_login',
  'login_options',
  'url_ready',
  'code_prompt',
  'login_success',
]);

export default function NotificationBell() {
  const navigate = useNavigate();
  const [needsAuth, setNeedsAuth] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // The panel is drawn at the page's top level (a portal), not inside the bar: the bar is its own stacking
  // context, so a panel inside it sat BEHIND the Agents page's floating agent pane however high its z-index.
  // z 80 clears the floating agent card (50, its toasts 60, its modals 70) and Arturo's pane (76). Placed
  // under the bell from the bell's position on screen when it opens, and again on resize.
  const [at, setAt] = useState<{ top: number; right: number } | null>(null);
  const place = useCallback(() => {
    const r = dropdownRef.current?.getBoundingClientRect();
    if (r) setAt({ top: Math.round(r.bottom + 8), right: Math.max(8, Math.round(window.innerWidth - r.right)) });
  }, []);
  useEffect(() => {
    if (!dropdownOpen) return;
    window.addEventListener('resize', place);
    window.visualViewport?.addEventListener('resize', place);
    return () => { window.removeEventListener('resize', place); window.visualViewport?.removeEventListener('resize', place); };
  }, [dropdownOpen, place]);

  // Poll auth state
  const poll = useCallback(async () => {
    try {
      const data = await getGmAuthState();
      setNeedsAuth(NEEDS_AUTH_STATES.has(data.state));
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    poll();
    const interval = setInterval(poll, 30_000);
    return () => clearInterval(interval);
  }, [poll]);

  // Fetch pending counts
  const { data: approvalStats } = useQuery({
    queryKey: ['unified-approvals-stats'],
    queryFn: () => fetchJson<{ by_status: Record<string, number>; total: number }>('/approvals/unified/stats'),
    refetchInterval: 10000,
  });

  const { data: qData } = useQuery({
    queryKey: ['questionnaires'],
    queryFn: () => fetchJson<{ questionnaires: any[]; pending: number }>('/questionnaires'),
    refetchInterval: 10000,
  });

  const pendingApprovals = approvalStats?.by_status?.pending || 0;
  const pendingQuestionnaires = qData?.pending || (qData?.questionnaires ?? []).filter((q: any) => q.status === 'pending').length || 0;
  const totalPending = pendingApprovals + pendingQuestionnaires + (needsAuth ? 1 : 0);

  // Close dropdown on click outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      const t = e.target as Node;
      if (dropdownRef.current && !dropdownRef.current.contains(t) && !panelRef.current?.contains(t)) {
        setDropdownOpen(false);
      }
    }
    if (dropdownOpen) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [dropdownOpen]);

  return (
    <>
      {/* A FLEX box, not a block: a block wrapper put the inline button on a text line, whose
          strut made the wrapper taller than the 40px button and nudged it off the row's centre. */}
      <div className="relative flex shrink-0 items-center" ref={dropdownRef}>
        <button
          onClick={() => { if (!dropdownOpen) place(); setDropdownOpen(!dropdownOpen); }}
          // The same icon set as the agent page's top bar (p-2 + a 24px icon = 40px box, the
          // foreground colour), so the bell reads as one of that row, not a smaller grey cousin.
          className="relative flex items-center justify-center p-2 text-foreground hover:bg-muted rounded-lg transition-colors"
          aria-label="Notifications"
        >
          <Bell size={24} />
          {totalPending > 0 && (
            // Absolutely positioned over the button's corner: it never takes layout space, so
            // a count appearing or growing cannot move the bell.
            <span className="pointer-events-none absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center text-[10px] font-bold leading-none rounded-full bg-red-500 text-white border-2 border-background px-1">
              {totalPending}
            </span>
          )}
        </button>

        {/* Dropdown */}
        {dropdownOpen && at && createPortal(
          <div ref={panelRef} style={{ top: at.top, right: at.right }}
               className="fixed w-72 max-w-[calc(100vw-16px)] overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl z-[80]">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <span className="text-sm font-semibold">Notifications</span>
              <button onClick={() => setDropdownOpen(false)} className="text-muted-foreground hover:text-foreground">
                <X size={14} />
              </button>
            </div>

            <div className="max-h-80 overflow-y-auto">
              {totalPending === 0 && (
                <p className="px-4 py-6 text-center text-sm text-muted-foreground">All clear</p>
              )}

              {pendingApprovals > 0 && (
                <button
                  onClick={() => { if (!isViewHidden('/inbox')) navigate('/inbox'); setDropdownOpen(false); }}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted"
                >
                  <div className="p-1.5 rounded-lg bg-violet-500/15">
                    <ShieldCheck size={14} className="text-violet-400" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-foreground">{pendingApprovals} pending approval{pendingApprovals !== 1 ? 's' : ''}</p>
                    <p className="text-xs text-muted-foreground">Review in Inbox</p>
                  </div>
                  <span className="bg-red-600 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">{pendingApprovals}</span>
                </button>
              )}

              {pendingQuestionnaires > 0 && (
                <button
                  onClick={() => { if (!isViewHidden('/inbox')) navigate('/inbox'); setDropdownOpen(false); }}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted"
                >
                  <div className="p-1.5 rounded-lg bg-amber-500/15">
                    <ClipboardList size={14} className="text-amber-400" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-foreground">{pendingQuestionnaires} questionnaire{pendingQuestionnaires !== 1 ? 's' : ''}</p>
                    <p className="text-xs text-muted-foreground">Needs your input</p>
                  </div>
                  <span className="bg-amber-600 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">{pendingQuestionnaires}</span>
                </button>
              )}

              {needsAuth && (
                <button
                  onClick={() => { setAuthOpen(true); setDropdownOpen(false); }}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted"
                >
                  <div className="p-1.5 rounded-lg bg-red-500/15">
                    <Bell size={14} className="text-red-400" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-foreground">Auth required</p>
                    <p className="text-xs text-muted-foreground">GM needs login</p>
                  </div>
                </button>
              )}
            </div>

            {totalPending > 0 && (
              <div className="border-t border-border px-4 py-2.5">
                <button
                  onClick={() => { if (!isViewHidden('/inbox')) navigate('/inbox'); setDropdownOpen(false); }}
                  className="text-xs font-medium text-primary hover:text-primary/80 transition-colors"
                >
                  View all in Inbox &rarr;
                </button>
              </div>
            )}
          </div>,
          document.body,
        )}
      </div>

      {authOpen && createPortal(
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <AuthFlow onClose={() => setAuthOpen(false)} />
        </div>,
        document.body,
      )}
    </>
  );
}
