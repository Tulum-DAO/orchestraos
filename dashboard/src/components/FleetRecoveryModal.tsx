import { useState, useEffect, useCallback } from 'react';

const BASE = '/api';

interface RosterEntry {
  session_id: string;
  tmux_session: string;
  cwd: string;
  model: string;
  status: string;
  launched_at?: string;
  died_at?: string;
}

export function FleetRecoveryModal() {
  const [open, setOpen] = useState(false);
  const [roster, setRoster] = useState<Record<string, RosterEntry>>({});
  const [copied, setCopied] = useState(false);

  const fetchRoster = useCallback(async () => {
    try {
      const res = await fetch(`${BASE}/agent-state/roster`);
      if (res.ok) setRoster(await res.json());
    } catch {}
  }, []);

  useEffect(() => { if (open) fetchRoster(); }, [open, fetchRoster]);

  const alive = Object.values(roster).filter(e => e.status === 'alive').length;
  const dead = Object.values(roster).filter(e => e.status === 'dead').length;
  const total = Object.keys(roster).length;

  const recoveryCmd = 'bash ~/scripts/agent-orchestra/scripts/roster-resume-all.sh';

  const handleCopy = () => {
    navigator.clipboard.writeText(recoveryCmd);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-2.5 py-1 text-[11px] font-medium rounded-md border transition-colors
                   border-neutral-700 bg-neutral-800 text-neutral-400 hover:text-neutral-200 hover:border-neutral-600"
      >
        ⚡ Recovery
      </button>
    );
  }

  return (
    <>
      {/* backdrop */}
      <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
      {/* modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
        <div
          className="bg-neutral-900 border border-neutral-700 rounded-xl shadow-2xl w-full max-w-lg max-h-[80vh] overflow-hidden"
          onClick={e => e.stopPropagation()}
        >
          {/* header */}
          <div className="flex items-center justify-between p-4 border-b border-neutral-800">
            <h3 className="text-sm font-semibold text-neutral-200 uppercase tracking-wider">Fleet Recovery</h3>
            <div className="flex items-center gap-3 text-xs">
              <span className="text-emerald-400">{alive} alive</span>
              {dead > 0 && <span className="text-red-400">{dead} dead</span>}
              <span className="text-neutral-500">{total} total</span>
            </div>
          </div>

          {/* body */}
          <div className="p-4 space-y-4 overflow-y-auto max-h-[60vh]">
            {/* command */}
            <div>
              <p className="text-xs text-neutral-500 mb-2">
                Paste this in any terminal to resume all dead agents from their exact sessions:
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-emerald-400 font-mono select-all">
                  {recoveryCmd}
                </code>
                <button
                  onClick={handleCopy}
                  className="px-3 py-2 text-xs font-medium rounded-lg border transition-colors
                             border-neutral-700 bg-neutral-800 text-neutral-300 hover:text-white hover:border-neutral-500"
                >
                  {copied ? '✓' : 'Copy'}
                </button>
              </div>
            </div>

            {/* roster table */}
            <table className="w-full text-xs">
              <thead>
                <tr className="text-neutral-500 uppercase tracking-wider">
                  <th className="text-left pb-1.5 font-medium">Agent</th>
                  <th className="text-left pb-1.5 font-medium">Status</th>
                  <th className="text-left pb-1.5 font-medium">Session</th>
                  <th className="text-left pb-1.5 font-medium">Model</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(roster)
                  .sort(([,a], [,b]) => (a.status === 'alive' ? -1 : 1) - (b.status === 'alive' ? -1 : 1))
                  .map(([name, entry]) => (
                    <tr key={name} className="border-t border-neutral-800/50">
                      <td className="py-1.5 text-neutral-200 font-medium">{name}</td>
                      <td className="py-1.5">
                        <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${entry.status === 'alive' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                        <span className={entry.status === 'alive' ? 'text-emerald-400' : 'text-red-400'}>
                          {entry.status}
                        </span>
                      </td>
                      <td className="py-1.5 text-neutral-500 font-mono truncate max-w-[120px]" title={entry.session_id}>
                        {entry.session_id?.slice(0, 8) || '—'}
                      </td>
                      <td className="py-1.5 text-neutral-400">{entry.model || '—'}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {/* footer */}
          <div className="p-4 border-t border-neutral-800 flex justify-end">
            <button
              onClick={() => setOpen(false)}
              className="px-4 py-1.5 text-xs font-medium rounded-lg border transition-colors
                         border-neutral-700 bg-neutral-800 text-neutral-300 hover:text-white"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
