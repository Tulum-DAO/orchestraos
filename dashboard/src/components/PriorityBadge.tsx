import { clsx } from 'clsx';
import { PRIORITY_COLORS } from '../lib/constants';

export function PriorityBadge({ priority }: { priority: string }) {
  return <span className={clsx('text-xs px-1.5 py-0.5 rounded font-medium', PRIORITY_COLORS[priority])}>{priority}</span>;
}
