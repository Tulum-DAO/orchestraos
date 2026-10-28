/*
 * Provider marks for the avatar badge: the owners' official files, bundled as asset URLs and drawn
 * unmodified (no recolouring or redrawing). Trademarks of their owners, used only to say which provider
 * runs a seat; sources and terms in src/assets/brand/README.md and the repo's NOTICE.
 *
 * Only the light/dark variant the owner ships is chosen: the OpenAI Blossom and the Grok mark come in a
 * dark mark for light surfaces and a light one for dark surfaces; the Claude spark and the Gemini
 * sparkle are the same file in both.
 */
import claudeSpark from '../../assets/brand/claude-spark-clay.svg';
import openaiBlack from '../../assets/brand/openai-blossom-black.svg';
import openaiWhite from '../../assets/brand/openai-blossom-white.svg';
import geminiSparkle from '../../assets/brand/gemini-sparkle.svg';
import grokDark from '../../assets/brand/grok-mark-dark.svg';
import grokLight from '../../assets/brand/grok-mark-light.svg';
import type { Theme } from './avatarCore';

export type ProviderId = 'claude' | 'openai' | 'gemini' | 'grok';

export const BRAND_FILES: Record<ProviderId, { light: string; dark: string; label: string }> = {
  claude: { light: claudeSpark, dark: claudeSpark, label: 'Anthropic Claude' },
  openai: { light: openaiBlack, dark: openaiWhite, label: 'OpenAI' },
  gemini: { light: geminiSparkle, dark: geminiSparkle, label: 'Google Gemini' },
  grok:   { light: grokDark, dark: grokLight, label: 'xAI Grok' },
};

// Runtime ids the rest of the app uses for the same providers (an agent row's `provider` is its runtime).
const ALIASES: Record<string, ProviderId> = {
  claude: 'claude', anthropic: 'claude',
  openai: 'openai', codex: 'openai', 'gpt-live': 'openai',
  gemini: 'gemini', google: 'gemini',
  grok: 'grok', xai: 'grok',
};

/** The badge's provider for a runtime or provider id, or null when it has no mark (no badge then). */
export function providerMark(id: string | null | undefined): ProviderId | null {
  if (!id) return null;
  return ALIASES[String(id).toLowerCase()] ?? null;
}

export function brandUrl(provider: ProviderId, theme: Theme): string {
  return BRAND_FILES[provider][theme];
}
