/** iOS chip rule (Sources/iOS/Shells.swift): "gen N" only when a seat has been reincarnated (N > 1). */
export function genLabel(generation: unknown): string | null {
  const g = typeof generation === 'number' ? generation : Number(generation);
  return Number.isFinite(g) && g > 1 ? `gen ${g}` : null;
}
