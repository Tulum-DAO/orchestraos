/**
 * modelPurpose.ts — the few words of purpose the model picker shows beside a model's name ("Deep work · long tasks").
 *
 * A small, hand-maintained table (kept by hand). Each entry follows how the VENDOR itself positions that model
 * family, in neutral words: Anthropic's Opus is its model for complex, long-running work, Sonnet its everyday model
 * balancing capability and speed, Haiku its fastest; Google's Pro is its model for complex reasoning, Flash its fast
 * one. Nothing about price, and no speed or quality claim beyond that positioning.
 *
 * A model with no entry gets no words, never a guess: Fable, Codex's GPT names and Grok have none until someone adds
 * one here from the vendor's own description. Matching is on the family word in the model's name, per runtime, so a new
 * version of a known family ("Opus 6") keeps its words without an edit.
 */

interface PurposeRule { runtime: string; family: RegExp; words: string }

export const PURPOSE_TABLE: readonly PurposeRule[] = [
  { runtime: 'claude', family: /\bopus\b/i, words: 'Deep work · long tasks' },
  { runtime: 'claude', family: /\bsonnet\b/i, words: 'Everyday · fast' },
  { runtime: 'claude', family: /\bhaiku\b/i, words: 'Quick replies' },
  { runtime: 'gemini', family: /\bpro\b/i, words: 'Complex reasoning' },
  { runtime: 'gemini', family: /\bflash\b/i, words: 'Everyday · fast' },
];

/** The words for a model, from its runtime and name ("Opus 5.5"); null when the table has none. */
export function modelPurpose(runtime: string, name: string): string | null {
  const rule = PURPOSE_TABLE.find((r) => r.runtime === runtime && r.family.test(name));
  return rule ? rule.words : null;
}
