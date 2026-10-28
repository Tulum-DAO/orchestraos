/**
 * Start a fresh Arturo conversation from outside the home (the sidebar's "New chat"): the same steps
 * as the home's own plus button. It becomes the browser's one current thread (lib/arturoCurrent), so
 * the home shows it whether it is already open or opens next, and the chosen brain is warmed for it.
 */
import { newConversationId } from './arturo';
import { arturoPrewarm } from './arturoStream';
import { toWireBrain } from './arturoBrain';
import { setCurrentThread } from './arturoCurrent';
import { useArturoBrain } from '../stores/arturoBrain';

export function startNewArturoChat(): string {
  const id = newConversationId('web');
  setCurrentThread(id);
  arturoPrewarm(id, toWireBrain(useArturoBrain.getState().choice));
  return id;
}
