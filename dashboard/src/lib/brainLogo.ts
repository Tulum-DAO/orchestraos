/**
 * Arturo's brain chip shows only the logo of the provider answering: the one picked in the model
 * sheet, or the default brain's provider from /api/arturo/health. Null when nothing is known yet
 * (the chip falls back to a generic glyph).
 */
export interface HealthBrain { kind?: string; provider?: string; runtime?: string }

export function brainProviderId(choice: { provider: string } | null | undefined,
                                defaultBrain: HealthBrain | null | undefined): string | null {
  if (choice?.provider) return choice.provider;
  if (!defaultBrain) return null;
  if (defaultBrain.provider) return defaultBrain.provider;
  if (defaultBrain.runtime) return defaultBrain.runtime;
  return defaultBrain.kind === 'api' ? 'gemini' : null;
}
