/**
 * What a card's popover (CardChip → CardPopFrame) hands the card drawn in it: Close the popover, and Go to card.
 *
 * The one mechanism every popover card goes through. A card frame (DecisionCard, MultiPartCard's Frame, a note
 * drawn in place of a card) reads it: present, the card's top-right corner holds Go to card and Close
 * (components/kit/CardPopControls.tsx) and a waiting card's Dismiss moves to its answer footer, left of Send.
 * Absent (a card in a chat, on Approvals or Inbox), the card is drawn as it always was: its × dismisses it.
 */
import { createContext, useContext } from 'react';

export interface CardPopControls {
  /** Closes the popover (focus goes back to the chip). Never dismisses the card. */
  onClose: () => void;
  /** Goes to where the card lives (and closes the popover). */
  onGoTo: () => void;
}

export const CardPopContext = createContext<CardPopControls | null>(null);

/** The popover's controls when this card is drawn in one, else null. */
export function useCardPop(): CardPopControls | null {
  return useContext(CardPopContext);
}
