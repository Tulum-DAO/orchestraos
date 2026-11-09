import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import CreateIssueButton from '../components/red-alert/CreateIssueButton';

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

type Report = {
  id: string;
  status?: string;
  class?: string;
  severity?: string;
  seats?: string[];
  symptom?: string;
  created_at?: string;
};

export default function RedAlerts() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['red-alert-reports'],
    queryFn: () => fetchJson<Report[]>('/red-alert/reports'),
    refetchInterval: 15000,
  });
  const [filter, setFilter] = useState('open');
  const items = (data || []).filter((r) => !filter || r.status === filter || filter === 'all');

  return (
    <div className="p-4 space-y-4 max-w-3xl">
      <h1 className="text-lg font-semibold">Red Alerts</h1>
      <p className="text-xs text-tone-400">
        Watchdog cards land in Approvals. This list is every report on disk.
        Create issue never auto-files — preview + confirm; repo setting defaults unset.
      </p>
      <div className="flex gap-2 text-xs">
        {['open', 'awaiting-approval', 'resolved', 'all'].map((k) => (
          <button key={k} type="button"
            className={`px-2 py-1 rounded border ${filter === k ? 'bg-tone-700' : 'border-tone-600'}`}
            onClick={() => setFilter(k)}>{k}</button>
        ))}
      </div>
      {isLoading && <div className="text-sm text-tone-400">Loading…</div>}
      {error && <div className="text-sm text-red-400">{String(error)}</div>}
      <ul className="space-y-3">
        {items.map((r) => (
          <li key={r.id} className="border border-tone-700 rounded p-3 text-sm">
            <div className="font-mono text-xs text-tone-400">{r.id}</div>
            <div className="font-medium">{r.class || r.severity} · {(r.seats || []).join(', ')}</div>
            <div className="text-tone-300 text-xs mt-1">{r.symptom}</div>
            <div className="text-tone-500 text-xs mt-1">{r.status} · {r.created_at}</div>
            <CreateIssueButton reportId={r.id} />
          </li>
        ))}
      </ul>
      {!isLoading && items.length === 0 && (
        <div className="text-sm text-tone-400">No reports{filter !== 'all' ? ` with status=${filter}` : ''}.</div>
      )}
    </div>
  );
}
