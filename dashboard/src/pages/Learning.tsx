import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import {
  Brain, Zap, TrendingUp, Shield,
  ChevronDown, ChevronRight,
} from 'lucide-react';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

// ── Types ──────────────────────────────────────────────────────

interface Snippet {
  id: number;
  tx_id: string;
  channel: string;
  intent: string;
  outcome_status: string;
  feedback_signal: string;
  score_composite: number;
  score_accuracy: number;
  score_efficiency: number;
  score_coherence: number;
  score_safety: number;
  ts: string;
  input_text: string;
  output_text: string;
}

interface Pattern {
  pattern_id: string;
  type: string;
  description: string;
  confidence: number;
  sample_size: number;
  status: string;
  detected_at: string;
}

interface Rule {
  rule_id: string;
  proposal_id: string;
  status: string;
  description: string;
  mechanism: string;
  rule_text: string;
  channel_scope: string;
  applied_at: string;
}

// ── Helpers ──────────────────────────────────────────────────────

const SCORE_COLOR = (s: number) =>
  s >= 0.8 ? 'text-green-400' : s >= 0.5 ? 'text-amber-400' : 'text-red-400';

const SCORE_BG = (s: number) =>
  s >= 0.8 ? 'bg-green-500' : s >= 0.5 ? 'bg-amber-500' : 'bg-red-500';

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ── Sub-tabs ────────────────────────────────────────────────────

const TABS = [
  { key: 'overview', label: 'Overview', icon: TrendingUp },
  { key: 'snippets', label: 'Snippets', icon: Zap },
  { key: 'patterns', label: 'Patterns', icon: Brain },
  { key: 'rules', label: 'Rules', icon: Shield },
] as const;

// ── Component ──────────────────────────────────────────────────

export default function Learning() {
  const [tab, setTab] = useState<string>('overview');
  const [channelFilter, setChannelFilter] = useState('all');
  const [expandedSnippet, setExpandedSnippet] = useState<number | null>(null);

  // Fetch all data
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['learning-stats'],
    queryFn: () => fetchJson<any>('/learning/stats'),
    refetchInterval: 15000,
  });

  const { data: snippetsData, isLoading: snippetsLoading } = useQuery({
    queryKey: ['learning-snippets'],
    queryFn: () => fetchJson<{ snippets: Snippet[] }>('/learning/snippets?limit=100'),
    refetchInterval: 15000,
  });

  const { data: patternsData } = useQuery({
    queryKey: ['learning-patterns'],
    queryFn: () => fetchJson<{ patterns: Pattern[] }>('/learning/patterns'),
    refetchInterval: 30000,
  });

  const { data: rulesData } = useQuery({
    queryKey: ['learning-rules'],
    queryFn: () => fetchJson<{ rules: Rule[] }>('/learning/rules'),
    refetchInterval: 30000,
  });

  const snippets = snippetsData?.snippets ?? [];
  const patterns = patternsData?.patterns ?? [];
  const rules = rulesData?.rules ?? [];

  // Derive channels for filter
  const channels = useMemo(() => {
    const set = new Set<string>();
    for (const s of snippets) if (s.channel) set.add(s.channel);
    return Array.from(set).sort();
  }, [snippets]);

  const filteredSnippets = useMemo(() => {
    if (channelFilter === 'all') return snippets;
    return snippets.filter(s => s.channel === channelFilter);
  }, [snippets, channelFilter]);

  // Stats
  const avgScore = stats?.totals?.avg_composite;
  const totalSnippets = stats?.totals?.total_snippets || 0;
  const totalFailures = stats?.totals?.total_failures || 0;
  const totalCorrections = stats?.totals?.total_corrections || 0;
  const activePatterns = stats?.active_patterns || 0;
  const activeRules = stats?.active_rules || 0;
  const pendingProposals = stats?.pending_proposals || 0;

  // Score trend from daily stats
  const dailyStats = stats?.daily ?? [];

  const isLoading = statsLoading || snippetsLoading;
  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-white">Learning Engine</h1>
        {pendingProposals > 0 && (
          <span className="bg-amber-600 text-white text-xs font-bold px-2 py-0.5 rounded-full">
            {pendingProposals} pending
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-neutral-900 rounded-lg p-1 overflow-x-auto">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap',
              tab === key ? 'bg-neutral-700 text-white' : 'text-neutral-500 hover:text-neutral-300'
            )}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {/* ═══ OVERVIEW TAB ═══ */}
      {tab === 'overview' && (
        <div className="space-y-6">
          {/* Stats grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatBox label="Avg Score" value={avgScore != null ? avgScore.toFixed(2) : '—'} color={avgScore >= 0.7 ? 'text-green-400' : avgScore >= 0.4 ? 'text-amber-400' : 'text-red-400'} />
            <StatBox label="Snippets" value={totalSnippets} color="text-neutral-300" />
            <StatBox label="Failures" value={totalFailures} color={totalFailures > 5 ? 'text-red-400' : 'text-neutral-300'} />
            <StatBox label="Corrections" value={totalCorrections} color="text-amber-400" />
            <StatBox label="Patterns" value={activePatterns} color="text-blue-400" />
            <StatBox label="Active Rules" value={activeRules} color="text-green-400" />
          </div>

          {/* Score trend */}
          {dailyStats.length > 0 && (
            <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-5">
              <h2 className="text-sm font-medium text-neutral-500 uppercase tracking-wide mb-4">Score Trend (7d)</h2>
              <div className="flex items-end gap-1 h-24">
                {dailyStats.slice(0, 21).reverse().map((d: any, i: number) => {
                  const score = d.avg_score ?? d.avg_composite ?? 0;
                  const height = Math.max(4, score * 100);
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center gap-1" title={`${d.date}: ${score.toFixed(2)}`}>
                      <div className={clsx('w-full rounded-t', SCORE_BG(score))} style={{ height: `${height}%` }} />
                    </div>
                  );
                })}
              </div>
              <div className="flex justify-between mt-1 text-[10px] text-neutral-600">
                <span>7d ago</span>
                <span>today</span>
              </div>
            </div>
          )}

          {/* Active rules summary */}
          {rules.length > 0 && (
            <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-5">
              <h2 className="text-sm font-medium text-neutral-500 uppercase tracking-wide mb-3">Active Rules</h2>
              <div className="space-y-2">
                {rules.slice(0, 5).map((r) => (
                  <div key={r.rule_id} className="flex items-start gap-3 bg-neutral-800 rounded-lg px-3 py-2.5">
                    <Shield size={14} className="text-green-500 shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-neutral-200 line-clamp-1">{r.description}</p>
                      <p className="text-xs text-neutral-500 mt-0.5">
                        {r.mechanism} · {r.status} · {relativeTime(r.applied_at)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ═══ SNIPPETS TAB ═══ */}
      {tab === 'snippets' && (
        <div className="space-y-4">
          {/* Filter */}
          <div className="flex items-center gap-3">
            <select
              value={channelFilter}
              onChange={(e) => setChannelFilter(e.target.value)}
              className="bg-neutral-800 text-neutral-300 text-sm rounded-lg px-3 py-1.5 border border-neutral-700 focus:outline-none"
            >
              <option value="all">All channels</option>
              {channels.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <span className="text-xs text-neutral-500">{filteredSnippets.length} snippets</span>
          </div>

          {/* Snippet feed */}
          <div className="space-y-2">
            {filteredSnippets.map((s) => {
              const isExpanded = expandedSnippet === s.id;
              return (
                <div key={s.id} className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
                  <button onClick={() => setExpandedSnippet(isExpanded ? null : s.id)} className="w-full text-left p-3">
                    <div className="flex items-center gap-3">
                      {/* Score bar */}
                      <div className="w-10 text-center">
                        <span className={clsx('text-sm font-bold', SCORE_COLOR(s.score_composite))}>
                          {s.score_composite?.toFixed(1)}
                        </span>
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400 font-medium">{s.channel}</span>
                          <span className="text-xs text-neutral-500">{s.intent}</span>
                          {s.outcome_status && !['success', 'ok'].includes(s.outcome_status) && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">{s.outcome_status}</span>
                          )}
                          {s.feedback_signal === 'correction' && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">correction</span>
                          )}
                        </div>
                        <p className="text-xs text-neutral-500 mt-1 truncate">
                          {(s.input_text || '').slice(0, 120)}
                        </p>
                      </div>

                      <div className="text-xs text-neutral-600 shrink-0">{relativeTime(s.ts)}</div>
                      {isExpanded ? <ChevronDown size={14} className="text-neutral-600" /> : <ChevronRight size={14} className="text-neutral-600" />}
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-neutral-800 px-4 py-3 space-y-3">
                      {/* Score breakdown */}
                      <div className="grid grid-cols-4 gap-2">
                        <ScorePill label="Accuracy" value={s.score_accuracy} />
                        <ScorePill label="Efficiency" value={s.score_efficiency} />
                        <ScorePill label="Coherence" value={s.score_coherence} />
                        <ScorePill label="Safety" value={s.score_safety} />
                      </div>

                      {s.input_text && (
                        <div>
                          <p className="text-[10px] text-neutral-600 uppercase tracking-wider mb-1">Input</p>
                          <p className="text-xs text-neutral-300 bg-neutral-800 rounded-lg px-3 py-2 whitespace-pre-wrap">{s.input_text.slice(0, 500)}</p>
                        </div>
                      )}
                      {s.output_text && (
                        <div>
                          <p className="text-[10px] text-neutral-600 uppercase tracking-wider mb-1">Output</p>
                          <p className="text-xs text-neutral-300 bg-neutral-800 rounded-lg px-3 py-2 whitespace-pre-wrap">{s.output_text.slice(0, 500)}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}

            {filteredSnippets.length === 0 && (
              <p className="text-neutral-600 text-center py-8">No snippets found</p>
            )}
          </div>
        </div>
      )}

      {/* ═══ PATTERNS TAB ═══ */}
      {tab === 'patterns' && (
        <div className="space-y-3">
          {patterns.length === 0 && (
            <div className="flex flex-col items-center py-12 text-neutral-500">
              <Brain className="w-10 h-10 mb-2 text-neutral-600" />
              <p className="text-sm">No active patterns detected</p>
            </div>
          )}
          {patterns.map((p) => (
            <div key={p.pattern_id} className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={clsx('text-xs px-2 py-0.5 rounded font-medium',
                  p.type === 'failure' ? 'bg-red-500/15 text-red-400' :
                  p.type === 'correction' ? 'bg-amber-500/15 text-amber-400' :
                  p.type === 'inefficiency' ? 'bg-blue-500/15 text-blue-400' :
                  'bg-neutral-700 text-neutral-400'
                )}>
                  {p.type}
                </span>
                <span className="text-xs text-neutral-500">
                  confidence: <span className={SCORE_COLOR(p.confidence)}>{(p.confidence * 100).toFixed(0)}%</span>
                </span>
                <span className="text-xs text-neutral-600">{p.sample_size} samples</span>
              </div>
              <p className="text-sm text-neutral-200">{p.description}</p>
              <p className="text-xs text-neutral-600">{relativeTime(p.detected_at)}</p>
            </div>
          ))}
        </div>
      )}

      {/* ═══ RULES TAB ═══ */}
      {tab === 'rules' && (
        <div className="space-y-3">
          {rules.length === 0 && (
            <div className="flex flex-col items-center py-12 text-neutral-500">
              <Shield className="w-10 h-10 mb-2 text-neutral-600" />
              <p className="text-sm">No active rules</p>
            </div>
          )}
          {rules.map((r) => (
            <div key={r.rule_id} className="rounded-xl border border-neutral-800 bg-neutral-900 p-4 space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={clsx('text-xs px-2 py-0.5 rounded font-medium',
                  r.status === 'permanent' ? 'bg-green-500/15 text-green-400' : 'bg-amber-500/15 text-amber-400'
                )}>
                  {r.status}
                </span>
                <span className="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-400">{r.mechanism}</span>
                <span className="text-xs text-neutral-600">scope: {r.channel_scope}</span>
              </div>
              <p className="text-sm text-neutral-200">{r.description}</p>
              {r.rule_text && (
                <div className="bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2">
                  <p className="text-xs text-neutral-300 font-mono leading-relaxed">{r.rule_text}</p>
                </div>
              )}
              <p className="text-xs text-neutral-600">Applied {relativeTime(r.applied_at)}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Small components ────────────────────────────────────────────

function StatBox({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-xl px-3 py-3">
      <p className="text-[11px] text-neutral-500 mb-1">{label}</p>
      <p className={clsx('text-xl font-bold', color)}>{value}</p>
    </div>
  );
}

function ScorePill({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-neutral-800 rounded-lg px-2 py-1.5 text-center">
      <p className="text-[10px] text-neutral-500">{label}</p>
      <p className={clsx('text-sm font-bold', SCORE_COLOR(value || 0))}>{(value || 0).toFixed(2)}</p>
    </div>
  );
}
