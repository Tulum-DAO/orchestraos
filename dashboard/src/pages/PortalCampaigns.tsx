import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { Megaphone, DollarSign, TrendingUp, ImageIcon } from 'lucide-react';
import { clsx } from 'clsx';

interface Campaign {
  id: number;
  name: string;
  budget_set: number;
  spent: number;
  creatives: number;
  status: string;
}

interface ClientCampaigns {
  name: string;
  allocated: number;
  total_budget: number;
  total_spent: number;
  remaining: number;
  campaigns: Campaign[];
}

const STATUS_STYLES: Record<string, string> = {
  created: 'bg-blue-100 text-blue-700',
  active: 'bg-green-100 text-green-700',
  paused: 'bg-amber-100 text-amber-700',
  paused_no_funds: 'bg-red-100 text-red-700',
  completed: 'bg-neutral-100 text-neutral-600',
};

function fmt(n: number) {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 });
}

function StatusBadge({ status }: { status: string }) {
  const label = status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return (
    <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', STATUS_STYLES[status] || 'bg-neutral-100 text-neutral-600')}>
      {label}
    </span>
  );
}

function BudgetBar({ spent, budget }: { spent: number; budget: number }) {
  const pct = budget > 0 ? Math.min((spent / budget) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 bg-neutral-200 rounded-full overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all', pct > 90 ? 'bg-red-500' : pct > 60 ? 'bg-amber-500' : 'bg-green-500')}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-neutral-500 w-12 text-right">{pct.toFixed(0)}%</span>
    </div>
  );
}

export default function PortalCampaigns() {
  const { clientId } = useParams();

  const { data, isLoading, error } = useQuery<ClientCampaigns>({
    queryKey: ['portal-campaigns', clientId],
    queryFn: () => fetch(`/api/campaigns/${clientId}`).then(r => {
      if (!r.ok) throw new Error('Not found');
      return r.json();
    }),
  });

  if (isLoading) return <div className="text-neutral-500">Loading campaigns...</div>;
  if (error || !data) return <div className="text-neutral-500">No campaigns found for this account.</div>;

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-2xl font-bold text-neutral-900 flex items-center gap-2">
          <Megaphone size={22} className="text-neutral-600" />
          Ad Campaigns
        </h2>
        <p className="text-neutral-500 mt-1">Active display advertising campaigns for <span className="font-medium text-neutral-700">{data.name}</span>.</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-sm">
            <DollarSign size={14} />
            Budget
          </div>
          <p className="text-2xl font-bold text-neutral-900 mt-1">{fmt(data.total_budget)}</p>
        </div>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-sm">
            <TrendingUp size={14} />
            Spent
          </div>
          <p className="text-2xl font-bold text-neutral-900 mt-1">{fmt(data.total_spent)}</p>
        </div>
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex items-center gap-2 text-neutral-500 text-sm">
            <DollarSign size={14} />
            Remaining
          </div>
          <p className={clsx('text-2xl font-bold mt-1', data.remaining >= 0 ? 'text-green-600' : 'text-red-600')}>
            {fmt(data.remaining)}
          </p>
        </div>
      </div>

      {/* Campaign Cards */}
      <div className="space-y-4">
        {data.campaigns.map((camp) => (
          <div key={camp.id} className="rounded-xl border border-neutral-200 bg-white p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-neutral-900">{camp.name}</h3>
                <p className="text-xs text-neutral-500 mt-0.5">Campaign ID: {camp.id}</p>
              </div>
              <StatusBadge status={camp.status} />
            </div>

            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <span className="text-neutral-500">Budget</span>
                <p className="font-medium text-neutral-900">{fmt(camp.budget_set || 0)}</p>
              </div>
              <div>
                <span className="text-neutral-500">Spent</span>
                <p className="font-medium text-neutral-900">{fmt(camp.spent || 0)}</p>
              </div>
              <div>
                <span className="text-neutral-500">Creatives</span>
                <p className="font-medium text-neutral-900 flex items-center gap-1">
                  {camp.creatives ? <><ImageIcon size={14} /> {camp.creatives}</> : '—'}
                </p>
              </div>
            </div>

            <BudgetBar spent={camp.spent || 0} budget={camp.budget_set || 0} />
          </div>
        ))}
      </div>

      {data.campaigns.length === 0 && (
        <div className="text-center py-12 text-neutral-500">
          No campaigns are active yet. We'll notify you when your first campaign goes live.
        </div>
      )}
    </div>
  );
}
