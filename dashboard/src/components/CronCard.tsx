import { Clock } from 'lucide-react';

interface Props { name: string; frequencyLabel: string; description: string; }

export function CronCard({ name, frequencyLabel, description }: Props) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-tone-800 bg-tone-900/50">
      <Clock size={16} className="text-tone-500 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm">{name}</span>
          <span className="text-xs px-1.5 py-0.5 rounded bg-tone-800 text-tone-400">{frequencyLabel}</span>
        </div>
        <p className="text-xs text-tone-500 mt-0.5 line-clamp-2">{description}</p>
      </div>
    </div>
  );
}
