/**
 * voicePage.ts — what Settings › Voice (yours, on this device) and Settings › Server › Voice & calls (the install's)
 * show. Pure, so node tests read it: the voice provider's options with each one's one-line description (the voice
 * service's own copy, services/arturo/voice_vendor.py VENDOR_COPY, through the API's `choices`), and the words on the
 * personal rows. (No Call transcript row: nothing reads transcriptMode.)
 */
import type { SettingChoice } from './settingsApi';

export const UNAVAILABLE = 'Not available on this install';

export interface ProviderOption { value: string; label: string; description: string | null; disabled: boolean }

/** The provider's options: every one the voice service lists, an unavailable one greyed out and saying why. */
export function providerOptions(choices: readonly SettingChoice[] | undefined): ProviderOption[] {
  return (choices ?? []).map((c) => {
    // Its own trailing full stop dropped, so joining the unavailable words never gives "..".
    const t = typeof c.description === 'string' ? c.description.trim().replace(/\.+$/, '').trim() : '';
    const d = t || null;
    return {
      value: c.value, label: c.label,
      description: c.available ? d : d ? `${d}. ${UNAVAILABLE}.` : `${UNAVAILABLE}.`,
      disabled: !c.available,
    };
  });
}

/** The personal page's words (one place, so the page and its test agree). Every row is kept on this device
 *  (localStorage orchestra.agentSettings, stores/agentSettings.ts). */
export const VOICE_INTRO = 'How you talk to agents, on this device.';
/** Under the intro, before the link that opens the install's category. */
export const SERVER_VOICE_POINTER = 'Voice provider and limits are in';
export const MIC_DESC = "The mic button in an agent's chat box: hold it while you talk, or tap to start and tap again to stop. On this device.";
export const COLOUR_DESC = "The mic button's colour while it is listening. On this device.";
export const SERVER_VOICE_INTRO = 'Voice settings for the whole install. Only an admin can change them.';
