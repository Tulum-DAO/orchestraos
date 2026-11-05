/**
 * ObjectChip — the kit's chip for a reference to an object inside a message: an icon for its kind, a few words, and
 * a tone for where it stands. Cards are the first kind (components/kit/CardChip.tsx: "Which size? · Answered:
 * Circle"); agents, files, jobs and messages are the kinds it is shaped for (lib/cardRefs ObjectRef). It sits inline
 * in text, wraps as one word would, and never grows wider than the line.
 */
import { forwardRef, type ReactNode } from 'react';
import { clsx } from 'clsx';
import type { Skin } from './Markdown';

/** waiting: it needs someone; done: answered or finished; closed: dismissed, expired, gone; plain: no state yet. */
export type ChipTone = 'waiting' | 'done' | 'closed' | 'plain';

export interface ObjectChipProps {
  icon: ReactNode;
  label: string;
  /** Said to a screen reader and on hover: the whole label, and what a click does. */
  title?: string;
  tone?: ChipTone;
  skin?: Skin;
  onClick?: () => void;
  expanded?: boolean;
  /** For tests and the popover's anchor. */
  data?: Record<string, string>;
  /** Words only, not a control: a chip inside something that is itself a button (a tool card's row). */
  inert?: boolean;
}

const ObjectChip = forwardRef<HTMLButtonElement, ObjectChipProps>(function ObjectChip(
  { icon, label, title, tone = 'plain', skin = 'bubbles', onClick, expanded, data, inert }, ref) {
  const dataAttrs = Object.fromEntries(Object.entries(data ?? {}).map(([k, v]) => [`data-${k}`, v]));
  if (inert) {
    return (
      <span title={title ?? label} data-object-chip data-inert data-tone={tone} {...dataAttrs}
            className={clsx('kit-chip', 'is-inert', `kit-chip-${skin}`, `is-${tone}`)}>
        <span className="kit-chip-icon" aria-hidden>{icon}</span>
        <span className="kit-chip-label">{label}</span>
      </span>
    );
  }
  return (
    <button ref={ref} type="button" onClick={onClick} title={title ?? label} aria-label={title ?? label}
            aria-haspopup="dialog" aria-expanded={expanded} data-object-chip data-tone={tone} {...dataAttrs}
            className={clsx('kit-chip', `kit-chip-${skin}`, `is-${tone}`)}>
      <span className="kit-chip-icon" aria-hidden>{icon}</span>
      <span className="kit-chip-label">{label}</span>
    </button>
  );
});

export default ObjectChip;
