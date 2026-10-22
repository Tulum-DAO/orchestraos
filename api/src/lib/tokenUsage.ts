// Token usage on transcript items, in OpenTelemetry terms (the GenAI semantic conventions,
// gen_ai.usage.*): each key is the OTel attribute with `gen_ai.usage.` dropped and dots made
// underscores, and means what OTel means by it. Notably input_tokens is ALL input, cached included
// (OTel), which Claude's own `input_tokens` is not.
export interface TokenUsage {
  input_tokens: number;                  // gen_ai.usage.input_tokens
  output_tokens: number;                 // gen_ai.usage.output_tokens
  cache_read_input_tokens?: number;      // gen_ai.usage.cache_read.input_tokens
  cache_creation_input_tokens?: number;  // gen_ai.usage.cache_creation.input_tokens
}

const count = (v: unknown): number | undefined =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : undefined;

/** Claude's message.usage -> OTel. Needs both input and output counts, else none (no guessing). */
export function usageFromClaude(u: any): TokenUsage | undefined {
  const input = count(u?.input_tokens), output = count(u?.output_tokens);
  if (input === undefined || output === undefined) return undefined;
  const read = count(u?.cache_read_input_tokens), created = count(u?.cache_creation_input_tokens);
  const out: TokenUsage = { input_tokens: input + (read ?? 0) + (created ?? 0), output_tokens: output };
  if (read !== undefined) out.cache_read_input_tokens = read;
  if (created !== undefined) out.cache_creation_input_tokens = created;
  return out;
}

/** Codex's token_count last_token_usage -> OTel. Its input_tokens already includes the cached part. */
export function usageFromCodex(u: any): TokenUsage | undefined {
  const input = count(u?.input_tokens), output = count(u?.output_tokens);
  if (input === undefined || output === undefined) return undefined;
  const out: TokenUsage = { input_tokens: input, output_tokens: output };
  const read = count(u?.cached_input_tokens), created = count(u?.cache_write_input_tokens);
  if (read !== undefined) out.cache_read_input_tokens = read;
  if (created !== undefined) out.cache_creation_input_tokens = created;
  return out;
}
