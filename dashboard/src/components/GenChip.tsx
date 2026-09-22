/**
 * GenChip — which generation (epoch) of a seat is live. Parity with the iOS app
 * (Sources/iOS/Shells.swift: `Chip(text: "gen \(g)", color: Brand.mist)` when g > 1):
 * same words, same mist colour, shown only when the seat has been reincarnated at least once.
 * The number comes from the registry (`generation`), projected from the identity DB by the API.
 */
import { genLabel } from '../lib/genLabel.ts';

export function GenChip({ generation, className = '' }: { generation: unknown; className?: string }) {
  const label = genLabel(generation);
  if (!label) return null;
  return (
    <span className={`gen-chip inline-flex items-center rounded-full px-1.5 py-px text-[11px] font-medium shrink-0 ${className}`}
          style={{ color: '#8AA6A0', background: 'rgba(138,166,160,0.14)', border: '1px solid rgba(138,166,160,0.35)' }}
          title={`Generation ${Number(generation)} of this seat is live`} aria-label={label}>
      {label}
    </span>
  );
}
