import { Clock } from 'lucide-react';

interface Props { name: string; frequencyLabel: string; description: string; }

export function CronCard({ name, frequencyLabel, description }: Props) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-neutral-800 bg-neutral-900/50">
      <Clock size={16} className="text-neutral-500 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm">{name}</span>
          <span className="text-xs px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">{frequencyLabel}</span>
        </div>
        <p className="text-xs text-neutral-500 mt-0.5 line-clamp-2">{description}</p>
      </div>
    </div>
  );
}
