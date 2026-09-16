import { clsx } from 'clsx';
import { TIER_COLORS } from '../lib/constants';

export function TierBadge({ tier }: { tier: string }) {
  return <span className={clsx('text-xs px-1.5 py-0.5 rounded font-medium', TIER_COLORS[tier])}>{tier}</span>;
}
