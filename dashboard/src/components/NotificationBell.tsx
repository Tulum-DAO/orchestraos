import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Bell, ShieldCheck, ClipboardList, X } from 'lucide-react';
import { getGmAuthState } from '../lib/api';
import AuthFlow from './AuthFlow';

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
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    if (dropdownOpen) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [dropdownOpen]);

  return (
    <>
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className="relative p-1.5 text-neutral-400 hover:text-white transition-colors"
          aria-label="Notifications"
        >
          <Bell size={18} />
          {totalPending > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center text-[10px] font-bold rounded-full bg-red-500 text-white border-2 border-neutral-950 px-1">
              {totalPending}
            </span>
          )}
        </button>

        {/* Dropdown */}
        {dropdownOpen && (
          <div className="absolute right-0 top-full mt-2 w-72 bg-neutral-900 border border-neutral-700 rounded-xl shadow-2xl z-50 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
              <span className="text-sm font-semibold text-white">Notifications</span>
              <button onClick={() => setDropdownOpen(false)} className="text-neutral-500 hover:text-neutral-300">
                <X size={14} />
              </button>
            </div>

            <div className="max-h-80 overflow-y-auto">
              {totalPending === 0 && (
                <p className="px-4 py-6 text-sm text-neutral-500 text-center">All clear</p>
              )}

              {pendingApprovals > 0 && (
                <button
                  onClick={() => { navigate('/inbox'); setDropdownOpen(false); }}
                  className="w-full text-left px-4 py-3 hover:bg-neutral-800 transition-colors flex items-center gap-3"
                >
                  <div className="p-1.5 rounded-lg bg-violet-500/15">
                    <ShieldCheck size={14} className="text-violet-400" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-neutral-200">{pendingApprovals} pending approval{pendingApprovals !== 1 ? 's' : ''}</p>
                    <p className="text-xs text-neutral-500">Review in Inbox</p>
                  </div>
                  <span className="bg-red-600 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">{pendingApprovals}</span>
                </button>
              )}

              {pendingQuestionnaires > 0 && (
                <button
                  onClick={() => { navigate('/inbox'); setDropdownOpen(false); }}
                  className="w-full text-left px-4 py-3 hover:bg-neutral-800 transition-colors flex items-center gap-3"
                >
                  <div className="p-1.5 rounded-lg bg-amber-500/15">
                    <ClipboardList size={14} className="text-amber-400" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-neutral-200">{pendingQuestionnaires} questionnaire{pendingQuestionnaires !== 1 ? 's' : ''}</p>
                    <p className="text-xs text-neutral-500">Needs your input</p>
                  </div>
                  <span className="bg-amber-600 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">{pendingQuestionnaires}</span>
                </button>
              )}

              {needsAuth && (
                <button
                  onClick={() => { setAuthOpen(true); setDropdownOpen(false); }}
                  className="w-full text-left px-4 py-3 hover:bg-neutral-800 transition-colors flex items-center gap-3"
                >
                  <div className="p-1.5 rounded-lg bg-red-500/15">
                    <Bell size={14} className="text-red-400" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-neutral-200">Auth required</p>
                    <p className="text-xs text-neutral-500">GM needs login</p>
                  </div>
                </button>
              )}
            </div>

            {totalPending > 0 && (
              <div className="border-t border-neutral-800 px-4 py-2.5">
                <button
                  onClick={() => { navigate('/inbox'); setDropdownOpen(false); }}
                  className="text-xs text-violet-400 hover:text-violet-300 font-medium transition-colors"
                >
                  View all in Inbox &rarr;
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {authOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <AuthFlow onClose={() => setAuthOpen(false)} />
        </div>
      )}
    </>
  );
}
