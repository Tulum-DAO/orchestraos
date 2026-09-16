import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Megaphone, DollarSign, TrendingUp, Eye, ChevronDown, ChevronRight, ImageIcon } from 'lucide-react';
import { clsx } from 'clsx';

interface Campaign {
  id: number;
  name: string;
  budget_set: number;
  spent: number;
  creatives: number;
  status: string;
}

interface ClientData {
  name: string;
  allocated: number;
  campaigns: Campaign[];
}

interface CampaignsData {
  dsp_account: { id: number; platform: string } | null;
  total_deposited: number;
  total_allocated: number;
  unallocated: number;
  clients: Record<string, ClientData>;
  deposits: { amount: number; date: string; note: string }[];
  last_updated: string;
}

const STATUS_STYLES: Record<string, string> = {
  created: 'bg-blue-500/20 text-blue-400',
  active: 'bg-green-500/20 text-green-400',
  paused: 'bg-amber-500/20 text-amber-400',
  paused_no_funds: 'bg-red-500/20 text-red-400',
  completed: 'bg-neutral-500/20 text-neutral-400',
};

function fmt(n: number) {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

function StatusBadge({ status }: { status: string }) {
  const label = status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return (
    <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', STATUS_STYLES[status] || 'bg-neutral-700 text-neutral-300')}>
      {label}
    </span>
  );
}

function BudgetBar({ spent, budget }: { spent: number; budget: number }) {
  const pct = budget > 0 ? Math.min((spent / budget) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 bg-neutral-700 rounded-full overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all', pct > 90 ? 'bg-red-500' : pct > 60 ? 'bg-amber-500' : 'bg-green-500')}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-neutral-400 w-12 text-right">{pct.toFixed(0)}%</span>
    </div>
  );
}

function ClientSection({ slug, client }: { slug: string; client: ClientData }) {
  const [expanded, setExpanded] = useState(true);
  const totalSpent = client.campaigns.reduce((s, c) => s + (c.spent || 0), 0);

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-neutral-800/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <h3 className="font-semibold text-white">{client.name}</h3>
          <span className="text-xs text-neutral-500">{slug}</span>
          <span className="text-xs bg-neutral-800 text-neutral-400 px-2 py-0.5 rounded">
            {client.campaigns.length} campaign{client.campaigns.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <div className="text-right">
            <span className="text-neutral-500">Allocated</span>
            <span className="ml-2 text-white font-medium">{fmt(client.allocated)}</span>
          </div>
          <div className="text-right">
            <span className="text-neutral-500">Spent</span>
            <span className="ml-2 text-white font-medium">{fmt(totalSpent)}</span>
          </div>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-neutral-800">
          {client.campaigns.length === 0 ? (
            <p className="p-4 text-neutral-500 text-sm">No campaigns yet</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-neutral-500 text-xs border-b border-neutral-800">
                  <th className="text-left p-3 pl-4 font-medium">Campaign</th>
                  <th className="text-left p-3 font-medium">ID</th>
                  <th className="text-left p-3 font-medium">Status</th>
                  <th className="text-right p-3 font-medium">Budget</th>
                  <th className="text-right p-3 font-medium">Spent</th>
                  <th className="text-left p-3 font-medium w-40">Progress</th>
                  <th className="text-right p-3 pr-4 font-medium">Creatives</th>
                </tr>
              </thead>
              <tbody>
                {client.campaigns.map((camp) => (
                  <tr key={camp.id} className="border-b border-neutral-800/50 hover:bg-neutral-800/30">
                    <td className="p-3 pl-4 text-white font-medium">{camp.name}</td>
                    <td className="p-3 text-neutral-400 font-mono text-xs">{camp.id}</td>
                    <td className="p-3"><StatusBadge status={camp.status} /></td>
                    <td className="p-3 text-right text-neutral-300">{fmt(camp.budget_set || 0)}</td>
                    <td className="p-3 text-right text-neutral-300">{fmt(camp.spent || 0)}</td>
                    <td className="p-3"><BudgetBar spent={camp.spent || 0} budget={camp.budget_set || 0} /></td>
                    <td className="p-3 pr-4 text-right">
                      {camp.creatives ? (
                        <span className="flex items-center justify-end gap-1 text-neutral-400">
                          <ImageIcon size={12} />
                          {camp.creatives}
                        </span>
                      ) : (
                        <span className="text-neutral-600">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export default function Campaigns() {
  const { data, isLoading } = useQuery<CampaignsData>({
    queryKey: ['campaigns'],
    queryFn: () => fetch('/api/campaigns').then(r => r.json()),
  });

  if (isLoading || !data) {
    return <div className="p-6 text-neutral-500">Loading campaigns...</div>;
  }

  const clientEntries = Object.entries(data.clients || {});

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Megaphone size={20} />
            Campaigns
          </h1>
          <p className="text-neutral-500 text-sm mt-1">
            DSP Account {data.dsp_account?.id} — {data.dsp_account?.platform}
          </p>
        </div>
        {data.last_updated && (
          <span className="text-xs text-neutral-600">
            Updated {new Date(data.last_updated).toLocaleString()}
          </span>
        )}
      </div>

      {/* Account Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-xs">
            <DollarSign size={12} />
            Total Deposited
          </div>
          <p className="text-xl font-bold text-white mt-1">{fmt(data.total_deposited)}</p>
        </div>
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-xs">
            <TrendingUp size={12} />
            Allocated
          </div>
          <p className="text-xl font-bold text-white mt-1">{fmt(data.total_allocated)}</p>
        </div>
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-xs">
            <DollarSign size={12} />
            Unallocated
          </div>
          <p className={clsx('text-xl font-bold mt-1', data.unallocated > 0 ? 'text-green-400' : 'text-red-400')}>
            {fmt(data.unallocated)}
          </p>
        </div>
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-xs">
            <Eye size={12} />
            Active Clients
          </div>
          <p className="text-xl font-bold text-white mt-1">{clientEntries.length}</p>
        </div>
      </div>

      {/* Deposit History */}
      {data.deposits.length > 0 && (
        <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-4">
          <h3 className="text-sm font-medium text-neutral-400 mb-2">Deposit History</h3>
          <div className="flex flex-wrap gap-3">
            {data.deposits.map((d, i) => (
              <div key={i} className="text-xs bg-neutral-800 rounded px-3 py-1.5">
                <span className="text-green-400 font-medium">+{fmt(d.amount)}</span>
                <span className="text-neutral-500 ml-2">{d.date}</span>
                {d.note && <span className="text-neutral-600 ml-1">— {d.note}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Client Sections */}
      <div className="space-y-4">
        {clientEntries.map(([slug, client]) => (
          <ClientSection key={slug} slug={slug} client={client} />
        ))}
      </div>
    </div>
  );
}
