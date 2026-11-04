/**
 * The popover's own controls, drawn inside the card (components/kit/cardPopContext.ts): Go to card, then Close, at
 * the card's top right where a waiting card's × sits outside a popover, the same size and look as that ×. Always
 * shown, on every popover card (waiting, answered, dismissed). Outside a popover they draw nothing.
 */
import type { ReactNode } from 'react';
import { clsx } from 'clsx';
import { ArrowUpRight, X } from 'lucide-react';
import { useCardPop } from './cardPopContext';

/** The ×'s look (DecisionCard DismissButton): a 28px circle with a 44px hit area, quiet until hovered. */
const ROUND = 'hit-44 w-7 h-7 flex items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground';

/** Go to card and Close, for a card's header. `className` places the pair in that header (its edge offsets). On
 *  touch they stand 16px apart, so their 44px tap areas (hit-44) do not overlap. */
export function PopHeaderControls({ className }: { className?: string }) {
  const pop = useCardPop();
  if (!pop) return null;
  return (
    <div className={clsx('touch-compact shrink-0 flex items-center gap-2 pointer-coarse:gap-4', className)} data-card-pop-controls>
      <button type="button" onClick={pop.onGoTo} aria-label="Go to card" title="Go to card" className={ROUND} data-card-goto>
        <ArrowUpRight size={15} aria-hidden />
      </button>
      <button type="button" onClick={pop.onClose} aria-label="Close" title="Close" className={ROUND} data-card-close>
        <X size={15} aria-hidden />
      </button>
    </div>
  );
}

/** Words drawn in place of a card (loading, couldn't load). Outside a popover: the plain line, as given. In one: on
 *  the card's surface, with the popover's controls at its top right like any card; `popClassName`, when given, is the
 *  line's look there instead of `className`. */
export function PopNote({ className, popClassName, children, ...data }: {
  className?: string; popClassName?: string; children: ReactNode;
} & Record<`data-${string}`, unknown>) {
  const pop = useCardPop();
  if (!pop) return <p className={className} {...data}>{children}</p>;
  return (
    <div className="flex items-center gap-1.5 rounded-2xl border border-border bg-card text-card-foreground pr-2" data-card-pop-note>
      <p className={clsx('min-w-0 flex-1 m-0', popClassName ?? className)} {...data}>{children}</p>
      <PopHeaderControls />
    </div>
  );
}
