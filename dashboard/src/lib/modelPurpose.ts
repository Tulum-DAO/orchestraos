/**
 * modelPurpose.ts — the few words of purpose the model picker shows beside a model's name ("Deep work · long tasks").
 *
 * First a small, hand-maintained table (kept by hand). Each entry follows how the VENDOR itself positions that
 * model family, in neutral words: Anthropic's Opus is its model for complex, long-running work, Sonnet its everyday
 * model balancing capability and speed, Haiku its fastest; Google's Pro is its model for complex reasoning, Flash its
 * fast one. Nothing about price, and no speed or quality claim beyond that positioning.
 *
 * A model the table has no entry for gets the CLI's OWN description of it, as the runtime's catalog probe reads it
 * (config/providers.json probe.description_key; Claude Code 2.1.295 says "For your toughest challenges" for Fable
 * 5.1), with a leading "<name> · " head dropped. No table entry and no description: no words, never a guess (Codex's
 * GPT names and Grok today). Matching is on the family word in the model's name, per runtime, so a new version of a
 * known family ("Opus 6") keeps its words without an edit.
 */

interface PurposeRule { runtime: string; family: RegExp; words: string }

export const PURPOSE_TABLE: readonly PurposeRule[] = [
  { runtime: 'claude', family: /\bopus\b/i, words: 'Deep work · long tasks' },
  { runtime: 'claude', family: /\bsonnet\b/i, words: 'Everyday · fast' },
  { runtime: 'claude', family: /\bhaiku\b/i, words: 'Quick replies' },
  { runtime: 'gemini', family: /\bpro\b/i, words: 'Complex reasoning' },
  { runtime: 'gemini', family: /\bflash\b/i, words: 'Everyday · fast' },
];

/** The CLI's description without a "<name/version> · " head ("Fable 5.1 · Most capable…" → "Most capable…"). */
function fromDescription(description: string | null | undefined): string | null {
  const d = description?.trim();
  if (!d) return null;
  const i = d.indexOf(' · ');
  const words = i > 0 && /\d/.test(d.slice(0, i)) ? d.slice(i + 3).trim() : d;
  return words || null;
}

/** The words for a model, from its runtime and name ("Opus 5.5"), else from the CLI's own description; null when
 *  there is neither. */
export function modelPurpose(runtime: string, name: string, description?: string | null): string | null {
  const rule = PURPOSE_TABLE.find((r) => r.runtime === runtime && r.family.test(name));
  return rule ? rule.words : fromDescription(description);
}
