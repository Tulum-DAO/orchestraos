/**
 * switchCall.ts — one call at a time, never switched silently: calling someone else while a call is live asks
 * "End the call with <agent> and call <X>?" (components/call/SwitchCallDialog draws the question); only a yes ends
 * the first call.
 */
import { create } from 'zustand';

interface Ask { text: string; resolve: (yes: boolean) => void }
export const useSwitchAsk = create<{ ask: Ask | null }>(() => ({ ask: null }));

/** The question, answered: true to end the live call and make the new one. */
export function askSwitchCall(fromName: string, toName: string): Promise<boolean> {
  return new Promise((resolve) => {
    useSwitchAsk.getState().ask?.resolve(false);
    useSwitchAsk.setState({ ask: { text: `End the call with ${fromName} and call ${toName}?`, resolve } });
  });
}


/** Arturo's live call, as the rest of the app sees it. His call keeps its own session (hooks/useHandsFreeCall.ts);
 *  while it is up it is recorded here, so an agent's Call can ask before it is ended. */
export interface ArturoCallLive { name: string; end: () => void }
export const useArturoCall = create<{ live: ArturoCallLive | null }>(() => ({ live: null }));

/** Before an agent's call starts: with Arturo's call up, ask "End the call with Arturo and call <agent>?". True when
 *  the way is clear (no call, or a yes, which ends his call); false leaves his call as it is. */
export async function clearForAgentCall(toName: string): Promise<boolean> {
  const live = useArturoCall.getState().live;
  if (!live) return true;
  if (!(await askSwitchCall(live.name, toName))) return false;
  live.end();
  useArturoCall.setState({ live: null });
  return true;
}
