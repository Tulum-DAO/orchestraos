import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ClipboardList, CheckCircle2, Clock, ChevronDown, ChevronRight,
  ExternalLink, MessageSquare,
} from 'lucide-react';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

interface Questionnaire {
  id: string;
  title: string;
  description: string;
  question_count: number;
  status: 'pending' | 'completed';
  created_at: string;
  completed_at?: string;
  created_by: string;
  assigned_to: string;
}

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function Questionnaires() {
  const { data, isLoading } = useQuery({
    queryKey: ['questionnaires'],
    queryFn: () => fetchJson<{ questionnaires: Questionnaire[]; total: number; pending: number }>('/questionnaires'),
    refetchInterval: 10000,
  });

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, any>>({});

  // Fetch answers when expanding a completed questionnaire
  useEffect(() => {
    if (!expandedId) return;
    const q = questionnaires.find((q: Questionnaire) => q.id === expandedId);
    if (q?.status !== 'completed' || answers[expandedId]) return;
    fetchJson<any>(`/questionnaires/${expandedId}/response`)
      .then(data => { if (data?.submitted) setAnswers(prev => ({ ...prev, [expandedId]: data.answers })); })
      .catch(() => {});
  }, [expandedId]);

  if (isLoading) return <p className="text-neutral-500 p-8">Loading...</p>;

  const questionnaires = data?.questionnaires ?? [];
  const pending = questionnaires.filter((q: Questionnaire) => q.status === 'pending');
  const completed = questionnaires.filter((q: Questionnaire) => q.status === 'completed');

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-white">Questionnaires</h1>
        {pending.length > 0 && (
          <span className="bg-amber-600 text-white text-xs font-bold px-2 py-0.5 rounded-full animate-pulse">
            {pending.length}
          </span>
        )}
      </div>

      {/* Pending questionnaires */}
      {pending.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-amber-400 uppercase tracking-wide">Needs Your Input</h2>
          {pending.map((q: Questionnaire) => (
            <div key={q.id} className="rounded-xl border-l-4 border-l-amber-500 border border-neutral-800 bg-neutral-900 overflow-hidden">
              <div className="p-4 space-y-3">
                {q.created_by && q.created_by !== 'system' && (
                  <div className="text-[10px] font-mono font-medium text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded-md w-fit">
                    from {q.created_by}
                  </div>
                )}
                <div className="flex items-start gap-3">
                  <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400 shrink-0">
                    <ClipboardList size={18} />
                  </div>
                  <div className="flex-1">
                    <h3 className="text-sm font-medium text-neutral-100">{q.title}</h3>
                    <p className="text-xs text-neutral-500 mt-1">{q.description}</p>
                    <div className="flex items-center gap-3 mt-2 text-xs text-neutral-500">
                      <span className="flex items-center gap-1"><MessageSquare size={11} /> {q.question_count} questions</span>
                      <span className="flex items-center gap-1"><Clock size={11} /> {relativeTime(q.created_at)}</span>
                    </div>
                  </div>
                </div>

                <button
                  onClick={() => setExpandedId(expandedId === q.id ? null : q.id)}
                  className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 transition-colors font-medium"
                >
                  {expandedId === q.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {expandedId === q.id ? 'Close questionnaire' : 'Open questionnaire'}
                </button>

                {expandedId === q.id && (
                  <div className="rounded-lg overflow-hidden border border-neutral-700 bg-black">
                    <iframe
                      src={`/api/questionnaires/${q.id}/html`}
                      className="w-full border-0"
                      style={{ minHeight: '600px' }}
                      title={q.title}
                    />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {pending.length === 0 && completed.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-neutral-500">
          <ClipboardList className="w-12 h-12 mb-3 text-neutral-600" />
          <p className="text-lg font-medium text-neutral-400">No questionnaires</p>
          <p className="text-sm">The system will create questionnaires when it needs your input.</p>
        </div>
      )}

      {/* No pending but has completed */}
      {pending.length === 0 && completed.length > 0 && (
        <div className="flex flex-col items-center justify-center py-8 text-neutral-500">
          <CheckCircle2 className="w-10 h-10 mb-2 text-green-600" />
          <p className="text-sm text-neutral-400">All caught up</p>
        </div>
      )}

      {/* Completed questionnaires */}
      {completed.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-neutral-500 uppercase tracking-wide">Completed</h2>
          {completed.map((q: Questionnaire) => {
            const isOpen = expandedId === q.id;
            return (
              <div key={q.id} className="rounded-xl border border-neutral-800 bg-neutral-900/50 overflow-hidden">
                <div className="p-4">
                  {q.created_by && q.created_by !== 'system' && (
                    <div className="text-[10px] font-mono font-medium text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded-md w-fit mb-2">
                      from {q.created_by}
                    </div>
                  )}
                  <div className="flex items-center gap-3">
                    <CheckCircle2 size={16} className="text-green-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm text-neutral-300">{q.title}</h3>
                      <p className="text-xs text-neutral-600 mt-0.5">
                        {q.question_count} questions · completed {q.completed_at ? relativeTime(q.completed_at) : ''}
                      </p>
                    </div>
                    <button
                      onClick={() => setExpandedId(isOpen ? null : q.id)}
                      className="text-xs text-neutral-500 hover:text-neutral-300 flex items-center gap-1"
                    >
                      <ExternalLink size={12} />
                      {isOpen ? 'Hide' : 'Review'}
                    </button>
                  </div>

                  {isOpen && (
                    <div className="mt-3 rounded-lg overflow-hidden border border-neutral-700 bg-black">
                      <iframe
                        src={`/api/questionnaires/${q.id}/html`}
                        className="w-full border-0"
                        style={{ minHeight: '500px' }}
                        title={q.title}
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
