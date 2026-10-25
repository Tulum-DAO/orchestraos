/**
 * The "N messages waiting" banner above an agent's composer (GET/POST /api/agents/:id/held,
 * /deliver-now). Pure, so the wording is tested without a browser.
 */
export interface HeldState {
  count?: number;
  reason?: string | null;
  waiver?: { by?: string; expires_at?: number } | null;
}

export interface BannerView {
  text: string;
  canDeliver: boolean;
  pressed: boolean;
}

/** null when nothing is waiting. */
export function bannerView(h: HeldState | undefined, nowS: number = Date.now() / 1000): BannerView | null {
  const n = typeof h?.count === 'number' && h.count > 0 ? h.count : 0;
  if (!n) return null;
  const what = n === 1 ? '1 message waiting' : `${n} messages waiting`;
  const why = h?.reason ? `: ${h.reason}` : '';
  const pressed = !!(h?.waiver && typeof h.waiver.expires_at === 'number' && h.waiver.expires_at > nowS);
  if (pressed) {
    // Deliver now was pressed and mail is still here: the reason left is one we never override.
    return { text: `${what}, delivering as soon as it can${why}`, canDeliver: false, pressed: true };
  }
  return { text: `${what}${why}`, canDeliver: true, pressed: false };
}
