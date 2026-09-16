import { clsx } from 'clsx';

export function StatCard({ label, value, sub, alert }: { label: string; value: string | number; sub?: string; alert?: boolean }) {
  return (
    <div className={clsx('rounded-xl border p-4', alert ? 'border-red-800 bg-red-950/30' : 'border-neutral-800 bg-neutral-900')}>
      <p className="text-xs text-neutral-500 uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      {sub && <p className="text-xs text-neutral-500 mt-1">{sub}</p>}
    </div>
  );
}
