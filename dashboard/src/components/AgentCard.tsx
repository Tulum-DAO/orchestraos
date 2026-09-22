import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { clsx } from 'clsx';
import { MoreVertical, Play, Square, Terminal, Send, ChevronDown, ChevronUp, X, Maximize2, KeyRound, FileText, Copy, Download, RotateCcw, Eye, Edit3 } from 'lucide-react';
import { StatusDot } from './StatusDot';
import { AgentStatusDot } from './AgentStatusDot';
import { TierBadge } from './TierBadge';
import { normalizeAgentState, STATE_STYLE } from '../lib/agentStatus';
import { getAgentOutput, injectToAgent, messageAgent, sendKeyToAgent } from '../lib/api';
import ActionBar from './ActionBar';
import WebTerminal from './WebTerminal';
import TranscriptChatView from './chat/TranscriptChatView';
import ChatInput from './chat/ChatInput';
import AuthFlow from './AuthFlow';
import { logAction, trackRecentAgent } from '../lib/user-actions';
import { GenChip } from './GenChip';
import { useRecentAgents } from '../stores/recentAgents';
import { RecentAgentChips } from './RecentAgentChips';
import { setArturoFocus } from '../lib/arturo';

const PALETTE = [
  'bg-amber-600', 'bg-blue-600', 'bg-green-600', 'bg-purple-600',
  'bg-red-600', 'bg-cyan-600', 'bg-pink-600', 'bg-orange-600',
];

function hashName(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

const MACHINE_BADGE_STYLES: Record<string, string> = {
  'mac-online': 'bg-green-500/15 text-green-400',
  'mac-sleeping': 'bg-amber-500/15 text-amber-400',
  'mac-offline': 'bg-red-500/15 text-red-400',
  'vps': 'bg-blue-500/15 text-blue-400',
};

function getMachineBadgeStyle(machine: string, machineStatus: string): string {
  if (machine === 'vps') return MACHINE_BADGE_STYLES['vps'];
  return MACHINE_BADGE_STYLES[`mac-${machineStatus}`] || MACHINE_BADGE_STYLES['mac-offline'];
}

interface AgentCardProps {
  agent: any;
  onSpawn?: (id: string) => void;
  onKill?: (id: string) => void;
  spawning?: boolean;
  killing?: boolean;
}

export function AgentCard({ agent, onSpawn, onKill, spawning, killing }: AgentCardProps) {
  const [confirmKill, setConfirmKill] = useState<'kill' | 'restart' | null>(null);
  const initial = (agent.name || '?')[0].toUpperCase();
  const color = PALETTE[hashName(agent.name || '') % PALETTE.length];
  const displayName: string = agent.client && agent.id.includes(agent.client)
    ? agent.id.startsWith('pm-')
      ? `PM: ${(agent.client || '').split('-').map((w: string) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}`
      : agent.id.replace(`-${agent.client}`, '').split('-').map((w: string) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
    : (agent.name || agent.id);
  // "Working on:" is the most prominent line on a card, and current_task has no
  // expiry — gm's T0 card read "GM restarted — recovering from cascading
  // failure" for FOUR MONTHS (last_updated 2026-04-11), surviving ~9 generations
  // of that lineage. A confidently-worded stale string is worse than an empty
  // field: prominent, alarming, and false. So the sentence is trusted only as
  // long as its timestamp earns it.
  //   < 1h    : show as-is (genuinely current)
  //   1h – 7d : show WITH its age, so it reads as a claim about the past
  //   > 7d    : do not render the sentence at all — say the field is stale
  const rawTask = agent.current_task || agent.state || null;
  const taskAgeMs = agent.last_updated
    ? Date.now() - new Date(agent.last_updated).getTime()
    : null;
  const taskAgeLabel =
    taskAgeMs == null || !Number.isFinite(taskAgeMs) || taskAgeMs < 60 * 60_000
      ? null
      : taskAgeMs < 86_400_000
        ? `${Math.floor(taskAgeMs / 3_600_000)}h ago`
        : `${Math.floor(taskAgeMs / 86_400_000)}d ago`;
  const taskIsStale = taskAgeMs != null && taskAgeMs > 7 * 86_400_000;
  const staleTaskNote = taskIsStale && rawTask ? `No current task — last set ${taskAgeLabel}` : null;
  const taskText = taskIsStale ? null : rawTask;
  const truncatedTask = taskText
    ? taskText.length > 60 ? taskText.slice(0, 60) + '...' : taskText
    : null;
  const machine = agent.machine || '—';
  const machineStatus = agent.machine_status || 'offline';

  const isMacUnavailable = machine === 'mac' && (machineStatus === 'sleeping' || machineStatus === 'offline');
  const overlayLabel = machineStatus === 'sleeping' ? 'Mac sleeping' : 'Mac offline';

  const [expanded, setExpanded] = useState(false);
  const [focused, setFocused] = useState(false);
  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [, setOutputLoading] = useState(false);
  const [injectText, setInjectText] = useState('');
  const [injecting, setInjecting] = useState(false);
  const [injectResult, setInjectResult] = useState<string | null>(null);
  const [useInjectMode, setUseInjectMode] = useState(false);
  const [devMode, setDevMode] = useState(false);
  const [showAuthFlow, setShowAuthFlow] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [promptContent, setPromptContent] = useState<string | null>(null);
  const [showPromptModal, setShowPromptModal] = useState(false);
  const [promptEditing, setPromptEditing] = useState(false);
  const [, setRawOutputLines] = useState<string[]>([]);
  const outputRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const firstLoad = useRef(true);

  // --- Recent-agents store wiring (spec 2026-07-17-devmode-multi-agent-ux) ---
  const focusRequest = useRecentAgents((s) => s.focusRequest);
  const bumpRecent = useRecentAgents((s) => s.bump);
  const lockDeck = useRecentAgents((s) => s.lockDeck);
  const swipeDeeper = useRecentAgents((s) => s.swipeDeeper);
  const swipeBack = useRecentAgents((s) => s.swipeBack);
  const requestFocus = useRecentAgents((s) => s.requestFocus);
  const clearFocusRequest = useRecentAgents((s) => s.clearFocusRequest);
  const [swipePill, setSwipePill] = useState<string | null>(null);
  const touchRef = useRef<{ x: number; y: number; edge: 'left' | 'right' | null }>({ x: 0, y: 0, edge: null });

  // Status dots now bind to the v2 detector `agent.status` (see AgentStatusDot),
  // not a separate pane-scrape poll — fresh + app-aligned colors.

  // Navigating away unmounts the overlay without any close handler running, so clear the
  // published focus here too — a stale focus would make Arturo answer about an agent the
  // operator is no longer looking at, which is worse than having no context at all.
  useEffect(() => () => { setArturoFocus(null); }, []);

  // Cross-card focus coordination: chips/swipes ask the store; each card obeys.
  useEffect(() => {
    if (!focusRequest) return;
    if (focusRequest.id === agent.id) {
      setFocused(true);
      setArturoFocus({ kind: 'agent', id: agent.id, label: agent.name || agent.id });
      setDevMode(true);
      setUseInjectMode(true);
      if (!focusRequest.viaSwipe) {
        // Entry (chip click / normal open): counts as interaction + re-locks deck
        bumpRecent(agent.id);
        lockDeck(agent.id);
      }
      clearFocusRequest();
    } else if (focused && focusRequest.id !== agent.id) {
      setFocused(false); // another card is taking over
    }
  }, [focusRequest]); // eslint-disable-line react-hooks/exhaustive-deps

  /** The one way into this agent's chat: the "Open chat" button and a click on the card's name block. */
  const openChat = () => {
    setFocused(true);
    setArturoFocus({ kind: 'agent', id: agent.id, label: agent.name || agent.id });
    setExpanded(false);
    setUseInjectMode(true);
    logAction('agent.focus', agent.id, agent.name);
    trackRecentAgent(agent.id, agent.name || agent.id, agent.tier);
  };

  const openDevMode = () => {
    setFocused(true);
    setArturoFocus({ kind: 'agent', id: agent.id, label: agent.name || agent.id });
    setDevMode(true);
    setUseInjectMode(true);
    bumpRecent(agent.id);
    lockDeck(agent.id);
    logAction('agent.mode.dev', agent.id);
  };

  // Edge-swipe (mobile, dev mode): right-edge → deeper into locked deck,
  // left-edge → back. Vertical tmux scroll untouched (WebTerminal handles it).
  // Asymmetric edge zones: Safari reserves the outer ~25px on the LEFT for
  // its native back-swipe (can't be intercepted). Our left zone is wider so
  // the operator starts the swipe inboard of Safari's strip and still switches agents.
  const EDGE_RIGHT_PX = 28;
  const EDGE_LEFT_PX = 72;
  const SWIPE_THRESHOLD = 60;
  const onOverlayTouchStart = (e: React.TouchEvent) => {
    if (!devMode) return;
    const t = e.touches[0];
    const w = window.innerWidth;
    const edge = t.clientX > w - EDGE_RIGHT_PX ? 'right' : t.clientX < EDGE_LEFT_PX ? 'left' : null;
    touchRef.current = { x: t.clientX, y: t.clientY, edge };
  };
  const onOverlayTouchEnd = (e: React.TouchEvent) => {
    const { x, y, edge } = touchRef.current;
    if (!devMode || !edge) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - x;
    const dy = t.clientY - y;
    if (Math.abs(dx) < SWIPE_THRESHOLD || Math.abs(dy) > Math.abs(dx)) return;
    let target: string | null = null;
    if (edge === 'right' && dx < 0) target = swipeDeeper();
    if (edge === 'left' && dx > 0) target = swipeBack();
    if (target) {
      setSwipePill(target);
      setTimeout(() => setSwipePill(null), 1200);
      requestFocus(target, true); // viaSwipe: do NOT re-lock the deck
    }
    touchRef.current = { x: 0, y: 0, edge: null };
  };

  const isProtected = agent.tier === 'T0' || agent.always_on;

  useEffect(() => {
    if (!showMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setShowMenu(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showMenu]);

  const loadPrompt = async () => {
    try {
      const res = await fetch(`/api/agents/${agent.id}/prompt`);
      const data = await res.json();
      setPromptContent(data.content || 'No prompt file found');
    } catch {
      setPromptContent('Failed to load prompt');
    }
  };

  // Strip ANSI escape codes from terminal output
  const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '');

  // Parse numbered options from output lines (e.g. "1. Do something", "(2) Other thing", "3) Another")
  // parseNumberedOptions moved to output-parser.ts (used by ActionBar)

  // Poll for live output when expanded or focused
  useEffect(() => {
    if ((!expanded && !focused) || !agent.alive) return;

    let cancelled = false;
    const fetchOutput = async () => {
      setOutputLoading(true);
      try {
        const data = await getAgentOutput(agent.id, devMode ? 80 : 30);
        if (!cancelled) {
          // Save raw lines for dev mode
          const rawLines = data.lines || [];
          setRawOutputLines(rawLines);
          // Cleaned lines for chat mode
          const cleaned = rawLines.map(stripAnsi).filter((l: string) => {
            const t = l.trim();
            if (!t) return false;
            if (/^[─━═╌╍┄┅┈┉-]{4,}$/.test(t)) return false;
            if (/^[⏵⏴▶◀►◄]{2,}/.test(t) && /bypass permissions/.test(t)) return false;
            if (/^⬆/.test(t) && /context\)/.test(t)) return false;
            return true;
          });
          setOutputLines(cleaned);
          // Only auto-scroll on first load, not on polls (so user can read)
          if (firstLoad.current && outputRef.current) {
            outputRef.current.scrollTop = outputRef.current.scrollHeight;
            firstLoad.current = false;
          }
        }
      } catch {
        // ignore errors
      } finally {
        if (!cancelled) setOutputLoading(false);
      }
    };

    firstLoad.current = true;
    fetchOutput();
    const interval = setInterval(fetchOutput, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [expanded, focused, agent.id, agent.alive]);

  const handleInject = async () => {
    if (!injectText.trim()) return;
    logAction('agent.inject', agent.id, injectText.trim().slice(0, 100));
    setInjecting(true);
    setInjectResult(null);
    try {
      const result = await injectToAgent(agent.id, injectText.trim());
      setInjectResult(result.injected ? 'Sent' : 'Failed');
      setInjectText('');
    } catch (err: any) {
      setInjectResult('Error: ' + (err.message || 'unknown'));
    } finally {
      setInjecting(false);
      setTimeout(() => setInjectResult(null), 3000);
    }
  };

  const handleSend = async () => {
    if (!injectText.trim()) return;
    bumpRecent(agent.id); // the operator-interaction = recency (does NOT re-lock deck)
    if (useInjectMode) {
      return handleInject();
    }
    logAction('agent.message', agent.id, injectText.trim().slice(0, 100));
    setInjecting(true);
    setInjectResult(null);
    try {
      await messageAgent(agent.id, injectText.trim());
      setInjectResult('Sent');
      setInjectText('');
    } catch (err: any) {
      setInjectResult('Error: ' + (err.message || 'unknown'));
    } finally {
      setInjecting(false);
      setTimeout(() => setInjectResult(null), 3000);
    }
  };

  return (
    <div data-agent-id={agent.id} className="relative rounded-xl border border-neutral-800 bg-neutral-900 p-4 hover:border-neutral-700 transition">
      {/* Mac sleeping/offline overlay */}
      {isMacUnavailable && (
        <div className="absolute inset-0 bg-neutral-950/60 flex items-center justify-center rounded-xl z-10">
          <span className="text-sm font-medium text-neutral-400">{overlayLabel}</span>
        </div>
      )}

      {/* Top row */}
      <div className="flex items-center gap-3">
        <div className={clsx('w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0', color)}>
          {initial}
        </div>
        {/* The name is a link to the agent's page; a click anywhere else on this block opens the chat
            (an icon-only button was the only way in before, and testers never found it). */}
        <div
          className={clsx('flex-1 min-w-0', agent.alive && 'cursor-pointer')}
          onClick={() => { if (agent.alive) openChat(); }}
        >
          <div className="flex items-center gap-2">
            <Link
              to={`/agent/${encodeURIComponent(agent.id)}`}
              onClick={(e) => e.stopPropagation()}
              className="text-neutral-100 font-semibold truncate hover:underline"
              title={agent.name}
            >
              {displayName}
            </Link>
            <GenChip generation={agent.generation} />
            {agent.alive ? <AgentStatusDot status={agent.status} /> : <StatusDot status="stopped" />}
          </div>
        </div>
        <div className="relative" ref={menuRef}>
          <button onClick={() => { setShowMenu(!showMenu); logAction('agent.menu', agent.id); }} className="text-neutral-600 hover:text-neutral-400 transition shrink-0" aria-label="Menu">
            <MoreVertical size={16} />
          </button>
          {showMenu && (
            <div className="absolute right-0 top-8 z-40 bg-neutral-900 border border-neutral-700 rounded-lg shadow-xl py-1 w-48 text-xs">
              <button onClick={() => { loadPrompt(); setShowPromptModal(true); setPromptEditing(false); setShowMenu(false); logAction('agent.viewPrompt', agent.id); }} className="flex items-center gap-2 w-full px-3 py-2 text-left text-neutral-300 hover:bg-neutral-800 transition-colors">
                <Eye size={13} className="text-neutral-500" /> View Prompt
              </button>
              {!isProtected && (
                <button onClick={() => { loadPrompt(); setShowPromptModal(true); setPromptEditing(true); setShowMenu(false); logAction('agent.editPrompt', agent.id); }} className="flex items-center gap-2 w-full px-3 py-2 text-left text-neutral-300 hover:bg-neutral-800 transition-colors">
                  <Edit3 size={13} className="text-neutral-500" /> Edit Prompt
                </button>
              )}
              <div className="border-t border-neutral-800 my-1" />
              <button onClick={() => { navigator.clipboard.writeText(agent.tmux_session || agent.id); setShowMenu(false); logAction('agent.copySession', agent.id); }} className="flex items-center gap-2 w-full px-3 py-2 text-left text-neutral-300 hover:bg-neutral-800 transition-colors">
                <Copy size={13} className="text-neutral-500" /> Copy Session Name
              </button>
              <button onClick={() => { navigator.clipboard.writeText(agent.id); setShowMenu(false); logAction('agent.copyId', agent.id); }} className="flex items-center gap-2 w-full px-3 py-2 text-left text-neutral-300 hover:bg-neutral-800 transition-colors">
                <Copy size={13} className="text-neutral-500" /> Copy Agent ID
              </button>
              <button onClick={async () => { const res = await fetch(`/api/agents/${agent.id}/prompt`); const data = await res.json(); if (data.content) { const blob = new Blob([data.content], { type: 'text/markdown' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `${agent.id}-prompt.md`; a.click(); URL.revokeObjectURL(url); } setShowMenu(false); logAction('agent.downloadPrompt', agent.id); }} className="flex items-center gap-2 w-full px-3 py-2 text-left text-neutral-300 hover:bg-neutral-800 transition-colors">
                <Download size={13} className="text-neutral-500" /> Download Prompt
              </button>
              <div className="border-t border-neutral-800 my-1" />
              {agent.alive && onKill && onSpawn && confirmKill !== 'restart' && (
                <button onClick={() => { setConfirmKill('restart'); }} className="flex items-center gap-2 w-full px-3 py-2 text-left text-amber-400 hover:bg-neutral-800 transition-colors">
                  <RotateCcw size={13} /> Restart Agent
                </button>
              )}
              {agent.alive && onKill && onSpawn && confirmKill === 'restart' && (
                <div className="flex items-center gap-2 px-3 py-2">
                  <span className="text-[11px] text-amber-400">Restart?</span>
                  <button onClick={() => { onKill(agent.id); setTimeout(() => onSpawn(agent.id), 3000); setShowMenu(false); setConfirmKill(null); logAction('agent.restart', agent.id); }} className="text-xs px-2 py-1 rounded bg-amber-500/25 text-amber-400 hover:bg-amber-500/40 transition-colors font-medium">Yes</button>
                  <button onClick={() => setConfirmKill(null)} className="text-xs px-2 py-1 rounded bg-neutral-800 text-neutral-400 hover:bg-neutral-700 transition-colors font-medium">No</button>
                </div>
              )}
              <div className="px-3 py-2 text-[10px] text-neutral-600 border-t border-neutral-800 mt-1">
                <div>Tier: {agent.tier} · {machine}</div>
                <div>Session: {agent.tmux_session || agent.id}</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Subtitle: tier + machine + client + owner pills */}
      <div className="flex items-center gap-1.5 mt-2.5 flex-wrap">
        <TierBadge tier={agent.tier} />
        <span className={clsx(
          'text-[11px] px-1.5 py-0.5 rounded font-medium',
          getMachineBadgeStyle(machine, machineStatus)
        )}>
          {machine}
        </span>
        {(agent.tags || []).filter((t: string) => t.startsWith('client:')).map((t: string) => (
          <span key={t} className="text-[11px] px-1.5 py-0.5 rounded font-medium bg-violet-500/15 text-violet-400">
            {t.slice(7)}
          </span>
        ))}
        {(agent.tags || []).filter((t: string) => t.startsWith('owner:') && t !== 'owner:operator').map((t: string) => (
          <span key={t} className="text-[11px] px-1.5 py-0.5 rounded font-medium bg-cyan-500/15 text-cyan-400">
            {t.slice(6)}
          </span>
        ))}
      </div>

      {/* Working on + live state */}
      <div className="mt-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-neutral-500">Working on:</span>
          {/* These badges keyed off `agent.live_state`, a field that appears
              NOWHERE in the API — zero hits across api/src, null on all 172
              rows — so Active/Waiting/Idle have never once rendered. Rebound to
              the detector `status` through normalizeAgentState/STATE_STYLE: the
              same one truth the chips and the detail modal already share, so a
              third vocabulary cannot drift from them (the chip two-truths bug,
              d941183af/d97fb06fd, was exactly this shape). */}
          {agent.alive && (
            <span
              className={clsx(
                'flex items-center gap-1 text-[10px] font-medium',
                STATE_STYLE[normalizeAgentState(agent.status)].text,
              )}
            >
              <span
                className={clsx(
                  'w-1.5 h-1.5 rounded-full',
                  STATE_STYLE[normalizeAgentState(agent.status)].dot,
                )}
              />
              {STATE_STYLE[normalizeAgentState(agent.status)].label}
            </span>
          )}
        </div>
        <p className={clsx('text-sm mt-0.5 leading-snug', truncatedTask ? 'text-neutral-300' : 'text-neutral-600')}>
          {/* was 'Idle' — which reads as a STATE and now sits next to a state
              badge that can say "working", so an active agent with no recorded
              task rendered "working / Idle". The badge owns state; this line
              owns the task and says only whether one exists. */}
          {truncatedTask || staleTaskNote || 'No task recorded'}
          {truncatedTask && taskAgeLabel && (
            <span className="text-neutral-500"> · {taskAgeLabel}</span>
          )}
        </p>
      </div>

      {/* Bottom: kill left, expand/focus right */}
      <div className="mt-3 pt-3 border-t border-neutral-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {agent.alive && onKill && confirmKill === 'kill' && (
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-red-400 mr-1">Kill?</span>
              <button
                onClick={() => { onKill(agent.id); logAction('agent.kill', agent.id, agent.name); setConfirmKill(null); }}
                disabled={killing}
                className="flex items-center gap-1 text-xs px-2 py-1 min-h-[44px] rounded-md font-medium bg-red-500/25 text-red-400 hover:bg-red-500/40 transition-colors"
              >
                Yes
              </button>
              <button
                onClick={() => setConfirmKill(null)}
                className="flex items-center gap-1 text-xs px-2 py-1 min-h-[44px] rounded-md font-medium bg-neutral-800 text-neutral-400 hover:bg-neutral-700 transition-colors"
              >
                No
              </button>
            </div>
          )}
          {agent.alive && onKill && confirmKill !== 'kill' && (
            <button
              onClick={() => setConfirmKill('kill')}
              disabled={killing || isMacUnavailable}
              className={clsx('flex items-center gap-1 text-xs px-2.5 py-1 min-h-[44px] rounded-md font-medium transition-colors',
                killing ? 'bg-red-500/10 text-red-400/50 cursor-wait' : isMacUnavailable ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed' : 'bg-red-500/15 text-red-400 hover:bg-red-500/25'
              )}
            >
              <Square size={12} />
              {killing ? 'Killing...' : 'Kill'}
            </button>
          )}
          {!agent.alive && onSpawn && (
            <button
              onClick={() => { onSpawn(agent.id); logAction('agent.spawn', agent.id, agent.name); }}
              disabled={spawning || isMacUnavailable}
              className={clsx('flex items-center gap-1 text-xs px-2.5 py-1 min-h-[44px] rounded-md font-medium transition-colors',
                spawning ? 'bg-green-500/10 text-green-400/50 cursor-wait' : isMacUnavailable ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed' : 'bg-green-500/15 text-green-400 hover:bg-green-500/25'
              )}
            >
              <Play size={12} />
              {spawning ? 'Spawning...' : 'Spawn'}
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {agent.alive && (
            <>
              <button
                onClick={() => { setExpanded(!expanded); logAction(expanded ? 'agent.collapse' : 'agent.expand', agent.id, agent.name); if (!expanded) trackRecentAgent(agent.id, agent.name || agent.id, agent.tier); }}
                className="flex items-center gap-1 text-xs px-2.5 py-1 min-h-[44px] rounded-md font-medium bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors"
                aria-label={`${expanded ? 'Hide' : 'Show'} terminal for ${displayName}`}
                aria-expanded={expanded}
              >
                <Terminal size={12} />
                <span>Terminal</span>
                {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
              <button
                onClick={openChat}
                className="flex items-center gap-1 text-xs px-2.5 py-1 min-h-[44px] rounded-md font-medium bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors"
                aria-label={`Open chat with ${displayName}`}
              >
                <Maximize2 size={12} />
                <span>Open chat</span>
              </button>
            </>
          )}
        </div>
      </div>

      {/* Expanded: Chat Interface */}
      {expanded && agent.alive && (
        <div className="mt-3 pt-3 border-t border-neutral-800 space-y-3">
          {/* Chat Messages — transcript-driven renderer in compact mode */}
          <TranscriptChatView
            agentId={agent.id}
            compact
            state={agent.status || 'unknown'}
            strandedText={typeof agent.stranded === 'string' ? agent.stranded : agent.stranded?.text}
            pendingMenu={agent.pending_menu}
          />

          {/* Context-Aware ActionBar */}
          <ActionBar
            agentId={agent.id}
            outputLines={outputLines}
            onInject={(text) => injectToAgent(agent.id, text)}
            onSendKey={(key) => sendKeyToAgent(agent.id, key)}
            injectMode={useInjectMode}
          />

          {/* Message Input */}
          <div>
            <div className="flex gap-2">
              <textarea
                value={injectText}
                onChange={(e) => setInjectText(e.target.value)}
                placeholder="Message this agent..."
                rows={2}
                className="flex-1 bg-neutral-950 border border-neutral-800 rounded-lg px-2.5 py-1.5 text-xs text-neutral-300 placeholder-neutral-600 resize-none focus:outline-none focus:border-neutral-600"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
              <div className="flex flex-col gap-1 self-end">
                <button
                  onClick={handleSend}
                  disabled={injecting || !injectText.trim()}
                  className={clsx(
                    'flex items-center gap-1 px-3 py-1.5 min-h-[44px] rounded-lg text-xs font-medium transition-colors',
                    injecting || !injectText.trim()
                      ? 'bg-neutral-800 text-neutral-600 cursor-not-allowed'
                      : 'bg-blue-500/15 text-blue-400 hover:bg-blue-500/25'
                  )}
                >
                  <Send size={12} />
                  {injecting ? '...' : useInjectMode ? 'Inject' : 'Send'}
                </button>
                <button
                  onClick={() => setUseInjectMode(!useInjectMode)}
                  className={clsx(
                    'text-[10px] px-1.5 py-0.5 rounded transition-colors',
                    useInjectMode ? 'text-amber-400 bg-amber-500/10' : 'text-neutral-600 hover:text-neutral-400'
                  )}
                  title={useInjectMode ? 'Inject mode: sends directly to tmux session' : 'Inbox mode: sends to agent inbox'}
                >
                  {useInjectMode ? 'inject' : 'inbox'}
                </button>
              </div>
            </div>
            {injectResult && (
              <span className={clsx('text-[10px] mt-1 block', injectResult === 'Sent' ? 'text-green-500' : 'text-red-400')}>
                {injectResult}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Auth flow modal */}
      {showAuthFlow && (
        <div className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4" onClick={() => setShowAuthFlow(false)}>
          <div onClick={(e) => e.stopPropagation()}>
            <AuthFlow onClose={() => setShowAuthFlow(false)} />
          </div>
        </div>
      )}

      {/* Focused modal overlay */}
      {focused && agent.alive && (
        <div className={clsx(
          "fixed inset-0 z-50 bg-black/70 flex overscroll-none",
          devMode ? 'items-stretch justify-center p-0 overflow-hidden' : 'items-center justify-center p-4'
        )} onClick={() => { setFocused(false); setArturoFocus(null); }}>
          <div
            className={clsx(
              'bg-neutral-900 flex flex-col shadow-2xl',
              devMode
                ? 'w-full rounded-none border-0'
                : 'border border-neutral-700 rounded-2xl w-full max-w-2xl max-h-[90vh]'
            )}
            style={devMode ? { height: 'var(--vvh, 100vh)' } : undefined}
            onClick={(e) => e.stopPropagation()}
            onTouchStart={onOverlayTouchStart}
            onTouchEnd={onOverlayTouchEnd}
          >
            {/* Swipe destination pill (mobile deck navigation) */}
            {swipePill && (
              <div className="absolute top-14 left-1/2 -translate-x-1/2 z-[60] px-3 py-1.5 rounded-full bg-neutral-800/95 border border-neutral-600 text-xs text-neutral-100 shadow-lg pointer-events-none">
                {swipePill}
              </div>
            )}
            {/* Modal header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800 shrink-0">
              <div className="flex items-center gap-3">
                <div className={clsx('w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0', color)}>
                  {initial}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-neutral-100 font-semibold text-sm">{agent.name}</span>
                    <AgentStatusDot status={agent.status} />
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <TierBadge tier={agent.tier} />
                    <span className="text-[11px] text-neutral-500">{machine}</span>
                  </div>
                </div>
              </div>
              {/* Recent-4 agent chips (desktop, dev mode) — spec 2026-07-17 */}
              {devMode && <RecentAgentChips currentId={agent.id} />}
              <div className="flex items-center gap-2">
                {/* Re-auth (VPS agents only) */}
                {agent.machine === 'vps' && (
                  <button
                    onClick={() => setShowAuthFlow(true)}
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-md bg-amber-500/10 text-amber-400/80 hover:text-amber-300 transition-colors"
                    title="Re-authenticate Claude on VPS"
                  >
                    <KeyRound size={12} />
                  </button>
                )}
                {/* Chat / Dev toggle */}
                <div className="flex rounded-md border border-neutral-700 overflow-hidden">
                  <button
                    onClick={() => { setDevMode(false); setUseInjectMode(false); logAction('agent.mode.chat', agent.id); }}
                    className={clsx(
                      'text-[11px] px-2.5 py-1 transition-colors',
                      !devMode ? 'bg-neutral-700 text-neutral-100' : 'text-neutral-500 hover:text-neutral-300'
                    )}
                  >
                    Chat
                  </button>
                  <button
                    onClick={openDevMode}
                    className={clsx(
                      'text-[11px] px-2.5 py-1 transition-colors',
                      devMode ? 'bg-green-800 text-green-300' : 'text-neutral-500 hover:text-neutral-300'
                    )}
                  >
                    Dev
                  </button>
                </div>
                <button
                  onClick={() => { setFocused(false); setArturoFocus(null); }}
                  className="p-1.5 text-neutral-500 hover:text-neutral-200 hover:bg-neutral-800 rounded-lg transition-colors"
                >
                  <X size={16} />
                </button>
              </div>
            </div>

            {/* Output area — Chat or Dev mode */}
            {devMode ? (
              /* Dev Mode: real terminal via xterm.js + WebSocket */
              <div className="flex-1 min-h-0 bg-black overflow-hidden">
                <WebTerminal session={agent.tmux_session || agent.id.replace('unregistered:', '')} machine={agent.machine || 'mac'} />
              </div>
            ) : (
              /* Chat Mode: transcript-driven renderer (clean per-message isolation) */
              <div className="flex-1 min-h-0 flex flex-col">
                <TranscriptChatView
                  agentId={agent.id}
                  state={agent.status || 'unknown'}
                  strandedText={typeof agent.stranded === 'string' ? agent.stranded : agent.stranded?.text}
                  pendingMenu={agent.pending_menu}
                />
                <ChatInput
                  agentId={agent.id}
                  attachSupported={(agent.machine || 'vps') === 'vps'}
                  placeholder={agent.status === 'working' ? 'Agent is working (send will queue on the turn)…' : 'Message this agent…'}
                />
              </div>
            )}

            {/* ActionBar — always visible (mobile needs special keys even in Dev mode) */}
            <div className={clsx('px-3 py-2 border-t border-neutral-800 shrink-0', devMode && 'bg-neutral-950')}>
              <ActionBar
                agentId={agent.id}
                outputLines={outputLines}
                onInject={(text) => injectToAgent(agent.id, text)}
                onSendKey={(key) => sendKeyToAgent(agent.id, key)}
                injectMode={useInjectMode}
                devMode={devMode}
              />
            </div>

          </div>
        </div>
      )}

      {/* Prompt viewer/editor modal */}
      {showPromptModal && (
        <div className="fixed inset-0 z-[70] bg-black/70 flex items-center justify-center p-4" onClick={() => setShowPromptModal(false)}>
          <div className="bg-neutral-900 border border-neutral-700 rounded-2xl w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800 shrink-0">
              <div className="flex items-center gap-2">
                <FileText size={14} className="text-neutral-400" />
                <span className="text-sm font-semibold text-neutral-200">{promptEditing ? 'Edit' : 'View'} Prompt — {agent.name || agent.id}</span>
                {isProtected && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">read-only</span>}
              </div>
              <button onClick={() => setShowPromptModal(false)} className="text-neutral-500 hover:text-neutral-300"><X size={16} /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 min-h-0">
              {promptContent === null ? (
                <span className="text-xs text-neutral-500">Loading...</span>
              ) : promptEditing && !isProtected ? (
                <textarea value={promptContent} onChange={(e) => setPromptContent(e.target.value)} className="w-full h-full min-h-[300px] bg-neutral-950 text-neutral-200 text-xs font-mono p-3 rounded-lg border border-neutral-800 focus:border-neutral-600 focus:outline-none resize-none" spellCheck={false} />
              ) : (
                <pre className="text-xs text-neutral-300 font-mono whitespace-pre-wrap leading-relaxed">{promptContent}</pre>
              )}
            </div>
            {promptEditing && !isProtected && (
              <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-neutral-800 shrink-0">
                <button onClick={() => setShowPromptModal(false)} className="text-xs px-3 py-1.5 rounded-lg bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors">Cancel</button>
                <button onClick={async () => { await fetch(`/api/agents/${agent.id}/prompt`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: promptContent }) }); setShowPromptModal(false); logAction('agent.savePrompt', agent.id); }} className="text-xs px-3 py-1.5 rounded-lg bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 transition-colors font-medium">Save</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
