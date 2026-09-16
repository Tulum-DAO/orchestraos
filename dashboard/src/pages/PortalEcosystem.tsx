import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchClientEcosystem } from '../lib/api';
import { Globe, Link2, Users, AlertTriangle } from 'lucide-react';
import { clsx } from 'clsx';

const STATUS_DOT: Record<string, string> = {
  active: 'bg-green-500',
  setup: 'bg-amber-500',
  planned: 'bg-neutral-400',
  transition: 'bg-yellow-500',
};

function Dot({ status }: { status: string }) {
  return (
    <span
      className={clsx('inline-block w-2 h-2 rounded-full shrink-0', STATUS_DOT[status] || 'bg-neutral-300')}
      title={status}
    />
  );
}

export default function PortalEcosystem() {
  const { clientId } = useParams();
  const { data: eco, isLoading, error } = useQuery({
    queryKey: ['ecosystem', clientId],
    queryFn: () => fetchClientEcosystem(clientId!),
    enabled: !!clientId,
  });

  if (isLoading) return <p className="text-neutral-500 py-8">Loading ecosystem...</p>;
  if (error || !eco) return <p className="text-red-600 py-8">Failed to load ecosystem data.</p>;

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold text-neutral-900">Systems Overview</h2>
        <p className="text-neutral-500 mt-1">Infrastructure and integrations for {clientId}</p>
      </div>

      {/* Systems Table */}
      <section>
        <h3 className="text-sm font-medium text-neutral-500 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <Globe size={14} /> Systems
        </h3>
        <div className="rounded-xl border border-neutral-200 bg-white overflow-x-auto">
          <table className="w-full text-sm min-w-[500px]">
            <thead>
              <tr className="text-neutral-500 text-xs uppercase tracking-wider border-b border-neutral-200">
                <th className="text-left px-4 py-3 font-medium">Name</th>
                <th className="text-left px-4 py-3 font-medium">Platform</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {eco.systems?.map((s: any, i: number) => (
                <tr key={i} className="border-b border-neutral-100 last:border-0">
                  <td className="px-4 py-3 text-neutral-900 font-medium">{s.name}</td>
                  <td className="px-4 py-3 text-neutral-600">{s.platform}</td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-1.5">
                      <Dot status={s.status} />
                      <span className="text-neutral-600 text-xs capitalize">{s.status}</span>
                    </span>
                  </td>
                </tr>
              ))}
              {(!eco.systems || eco.systems.length === 0) && (
                <tr><td colSpan={3} className="px-4 py-6 text-neutral-400 text-center">No systems configured</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Integrations */}
      <section>
        <h3 className="text-sm font-medium text-neutral-500 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <Link2 size={14} /> Integrations
        </h3>
        <div className="rounded-xl border border-neutral-200 bg-white divide-y divide-neutral-100">
          {eco.integrations?.map((int: any, i: number) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3 text-sm">
              <Dot status={int.status} />
              <span className="text-neutral-900">{int.from}</span>
              <span className="text-neutral-300">&rarr;</span>
              <span className="text-neutral-900">{int.to}</span>
              {int.notes && (
                <span className="text-neutral-400 text-xs ml-auto">({int.notes})</span>
              )}
            </div>
          ))}
          {(!eco.integrations || eco.integrations.length === 0) && (
            <div className="px-4 py-6 text-neutral-400 text-center text-sm">No integrations configured</div>
          )}
        </div>
      </section>

      {/* Fragilities */}
      {eco.fragilities?.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-red-600 uppercase tracking-wide mb-3 flex items-center gap-1.5">
            <AlertTriangle size={14} /> Known Risks
          </h3>
          <div className="space-y-2">
            {eco.fragilities.map((f: string, i: number) => (
              <div
                key={i}
                className="flex items-start gap-2.5 text-sm bg-red-50 border border-red-200 rounded-lg px-4 py-3"
              >
                <AlertTriangle size={14} className="text-red-500 mt-0.5 shrink-0" />
                <span className="text-red-800">{f}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Contacts */}
      <section>
        <h3 className="text-sm font-medium text-neutral-500 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <Users size={14} /> Contacts
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {Object.entries(eco.contacts || {}).map(([key, contact]: [string, any]) => (
            <div key={key} className="rounded-xl border border-neutral-200 bg-white px-4 py-3">
              <div className="text-xs text-neutral-400 capitalize">{key}</div>
              <div className="text-sm font-medium text-neutral-900">{contact.name}</div>
              <div className="text-xs text-neutral-500">{contact.role}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
