// One emoji, as the reactions API accepts it (routes/reactions.ts) and as the chat recognises an agent's emoji-only
// answer to a reaction wake (lib/reaction-wake-text.ts silentReply, services/reaction-wake.ts).
const KEYCAP = /^[0-9#*]\uFE0F?\u20E3$/;
const PICTOGRAPH = /\p{Extended_Pictographic}|\p{Regional_Indicator}|⃣/u;

/** One emoji (skin tones, flags, keycaps and ZWJ sequences included): no letters, no spaces, at most 32 code units. */
export function isEmoji(v: unknown): v is string {
  if (typeof v !== 'string' || v.length === 0 || v.length > 32) return false;
  if (/[\x00-\x7f]/.test(v) && !KEYCAP.test(v)) return false;          // no ASCII, except inside a keycap
  if (/[\s\p{L}]/u.test(v) || !PICTOGRAPH.test(v)) return false;
  return [...new Intl.Segmenter('en', { granularity: 'grapheme' }).segment(v)].length === 1;
}
