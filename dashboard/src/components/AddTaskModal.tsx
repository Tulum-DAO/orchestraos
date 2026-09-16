import { useState, useEffect, useCallback } from 'react';
import { clsx } from 'clsx';
import { X, Zap, Loader2 } from 'lucide-react';
import { post } from '../lib/api';

const TYPES = [
  { value: 'deliverable', label: 'Deliverable', color: 'bg-emerald-500/15 text-emerald-400' },
  { value: 'internal', label: 'Internal', color: 'bg-blue-500/15 text-blue-400' },
  { value: 'question', label: 'Question', color: 'bg-amber-500/15 text-amber-400' },
  { value: 'bug', label: 'Bug', color: 'bg-red-500/15 text-red-400' },
] as const;

const PRIORITIES = [
  { value: 'critical', label: 'Critical', color: 'text-red-400' },
  { value: 'high', label: 'High', color: 'text-amber-400' },
  { value: 'medium', label: 'Medium', color: 'text-neutral-300' },
  { value: 'low', label: 'Low', color: 'text-neutral-500' },
] as const;

interface Analysis {
  type: string;
  client: string | null;
  client_name: string | null;
  priority: string;
  assignee: string | null;
  assignee_name: string | null;
  available_clients: { slug: string; name: string }[];
  available_types: string[];
}

export function AddTaskModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [description, setDescription] = useState('');
  const [type, setType] = useState('deliverable');
  const [priority, setPriority] = useState('medium');
  const [client, setClient] = useState('');
  const [assignee, setAssignee] = useState('');
  const [assigneeName, setAssigneeName] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [clients, setClients] = useState<{ slug: string; name: string }[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzed, setAnalyzed] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Load clients on mount
  useEffect(() => {
    post('/tasks/analyze', { description: 'list clients' }).then((r: Analysis) => {
      if (r.available_clients?.length) setClients(r.available_clients);
    }).catch(() => {});
  }, []);

  const analyze = useCallback(async (desc: string) => {
    if (desc.length < 5) return;
    setAnalyzing(true);
    try {
      const result: Analysis = await post('/tasks/analyze', { description: desc });
      setType(result.type);
      setPriority(result.priority);
      setClient(result.client || '');
      setAssignee(result.assignee || '');
      setAssigneeName(result.assignee_name || '');
      if (result.available_clients?.length) setClients(result.available_clients);
      setAnalyzed(true);
    } catch { /* keep current values */ }
    setAnalyzing(false);
  }, []);

  // Debounced analysis on description change
  useEffect(() => {
    if (!description || description.length < 5) { setAnalyzed(false); return; }
    const timer = setTimeout(() => analyze(description), 600);
    return () => clearTimeout(timer);
  }, [description, analyze]);

  const handleSubmit = async () => {
    if (!description.trim()) return;
    setSubmitting(true);
    try {
      await post('/tasks', {
        description: description.trim(),
        type,
        priority,
        client: client || null,
        assignee: assignee || null,
        due_date: dueDate || null,
      });
      setDescription('');
      setType('deliverable');
      setPriority('medium');
      setClient('');
      setAssignee('');
      setDueDate('');
      setAnalyzed(false);
      onCreated();
      onClose();
    } catch { /* */ }
    setSubmitting(false);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-neutral-900 border border-neutral-700 rounded-xl w-full max-w-lg mx-4 shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-800">
          <h2 className="text-base font-semibold text-white">Add Task</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300 transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Smart text input */}
          <div>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Describe the task naturally... e.g. 'Build 5 audience lists for Impossible Solutions'"
              className="w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2.5 text-sm text-neutral-100 placeholder:text-neutral-600 focus:outline-none focus:border-violet-500 resize-none"
              rows={3}
              autoFocus
            />
            {analyzing && (
              <div className="flex items-center gap-1.5 mt-1.5 text-xs text-violet-400">
                <Loader2 size={12} className="animate-spin" />
                Analyzing...
              </div>
            )}
            {analyzed && !analyzing && (
              <div className="flex items-center gap-1.5 mt-1.5 text-xs text-emerald-400">
                <Zap size={12} />
                Auto-filled from description
              </div>
            )}
          </div>

          {/* Auto-filled fields */}
          <div className="grid grid-cols-2 gap-3">
            {/* Type */}
            <div>
              <label className="block text-[11px] text-neutral-500 mb-1 uppercase tracking-wider">Type</label>
              <div className="flex flex-wrap gap-1.5">
                {TYPES.map(t => (
                  <button
                    key={t.value}
                    onClick={() => setType(t.value)}
                    className={clsx(
                      'px-2.5 py-1 rounded text-[11px] font-medium transition-all',
                      type === t.value ? t.color + ' ring-1 ring-current' : 'bg-neutral-800 text-neutral-500 hover:text-neutral-300'
                    )}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Priority */}
            <div>
              <label className="block text-[11px] text-neutral-500 mb-1 uppercase tracking-wider">Priority</label>
              <select
                value={priority}
                onChange={e => setPriority(e.target.value)}
                className="w-full bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-neutral-500"
              >
                {PRIORITIES.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>

            {/* Client */}
            <div>
              <label className="block text-[11px] text-neutral-500 mb-1 uppercase tracking-wider">Client</label>
              <select
                value={client}
                onChange={e => setClient(e.target.value)}
                className="w-full bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-neutral-500"
              >
                <option value="">None / Internal</option>
                {clients.map(c => (
                  <option key={c.slug} value={c.slug}>{c.name}</option>
                ))}
              </select>
            </div>

            {/* Assignee */}
            <div>
              <label className="block text-[11px] text-neutral-500 mb-1 uppercase tracking-wider">Route to</label>
              <div className="flex items-center gap-2">
                <input
                  value={assignee}
                  onChange={e => { setAssignee(e.target.value); setAssigneeName(''); }}
                  placeholder="Auto-assigned"
                  className="flex-1 bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-neutral-500"
                />
              </div>
              {assigneeName && assignee && (
                <p className="text-[10px] text-neutral-500 mt-0.5">{assigneeName}</p>
              )}
            </div>

            {/* Due date */}
            <div className="col-span-2">
              <label className="block text-[11px] text-neutral-500 mb-1 uppercase tracking-wider">Due date (optional)</label>
              <input
                type="date"
                value={dueDate}
                onChange={e => setDueDate(e.target.value)}
                className="bg-neutral-800 border border-neutral-700 text-neutral-300 text-sm rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-neutral-500"
              />
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-neutral-800">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm text-neutral-400 hover:text-neutral-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!description.trim() || submitting}
            className={clsx(
              'px-4 py-1.5 text-sm font-medium rounded-lg transition-all',
              description.trim() && !submitting
                ? 'bg-violet-600 text-white hover:bg-violet-500'
                : 'bg-neutral-800 text-neutral-600 cursor-not-allowed'
            )}
          >
            {submitting ? 'Creating...' : 'Create Task'}
          </button>
        </div>
      </div>
    </div>
  );
}
