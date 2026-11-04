/**
 * CardPopFrame — the frame of a card's popover (CardChip): no box of its own. The card is the only surface, lifted
 * off the page by a popover shadow on its container. The frame draws no controls either: it hands Close and Go to
 * card to the card through CardPopContext (./cardPopContext.ts), and the card draws them in its own header, top
 * right (Go to card, then Close, where its × sits outside a popover); a waiting card's Dismiss moves to its answer
 * footer, left of Send. The frame takes focus when it opens (tabIndex -1), so a keyboard is in it.
 */
import type { CSSProperties, ReactNode, RefObject } from 'react';
import { CardPopContext } from './cardPopContext';

export interface CardPopFrameProps {
  id: string;
  label: string;
  style?: CSSProperties;
  boxRef?: RefObject<HTMLDivElement | null>;
  onGoTo: () => void;
  onClose: () => void;
  children: ReactNode;
}

export default function CardPopFrame({ id, label, style, boxRef, onGoTo, onClose, children }: CardPopFrameProps) {
  return (
    <div ref={boxRef} className="kit-card-pop" data-cap-root style={style} role="dialog" tabIndex={-1} aria-label={label} data-card-pop={id}>
      <div className="kit-card-pop-card" data-card-pop-card>
        <CardPopContext.Provider value={{ onClose, onGoTo }}>{children}</CardPopContext.Provider>
      </div>
    </div>
  );
}
