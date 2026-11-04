/**
 * The pill buttons of a card's answer footer (MultiPartCard: Back, Skip, Next / Send; a popover card's Dismiss,
 * DecisionCard DismissButton): one height, one radius. Quiet for the secondary ones, filled for the main one.
 */
export const PILL = 'hit-44 text-xs font-medium px-3 py-1 rounded-full border transition-colors disabled:opacity-40';
export const PILL_QUIET = `${PILL} border-border text-muted-foreground hover:bg-muted hover:text-foreground`;
export const PILL_MAIN = `${PILL} border-info bg-info text-info-foreground hover:bg-info/90`;
