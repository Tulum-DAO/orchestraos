// An agent's answer to a reaction wake that is not a message: NO_REPLY, a "no reply" phrase, or one emoji. The API
// hides it from the chat (api/src/lib/reaction-wake-text.ts silentReply, chat-transcript markWakeReplies) and stores an
// emoji as the agent's reaction. While such an answer is still being written, the chat must not say "Typing…" or draw
// it as a live bubble (liveTextOf). Both sides run every case in contract/wake-reply-cases.json, so they cannot drift.

const NO_REPLY = 'NO_REPLY';
const NO_REPLY_PHRASE = /^\(?\s*no (reply|response)( needed| required)?\s*\)?\.?$/i;
const KEYCAP = /^[0-9#*]️?⃣$/;
const PICTOGRAPH = /\p{Extended_Pictographic}|\p{Regional_Indicator}|⃣/u;

/** One emoji, as the API's lib/emoji.ts isEmoji. */
function isEmoji(v: string): boolean {
  if (v.length === 0 || v.length > 32) return false;
  if ([...v].some((ch) => ch.charCodeAt(0) < 0x80) && !KEYCAP.test(v)) return false;   // no ASCII, except in a keycap
  if (/[\s\p{L}]/u.test(v) || !PICTOGRAPH.test(v)) return false;
  return [...new Intl.Segmenter('en', { granularity: 'grapheme' }).segment(v)].length === 1;
}

/** The whole answer's class, exactly as the API's silentReply: 'silent', 'emoji', or null (a real message). */
export function quietAnswer(text: unknown): 'silent' | 'emoji' | null {
  if (typeof text !== 'string') return null;
  const t = text.trim();
  if (!t) return null;
  if (t === NO_REPLY || NO_REPLY_PHRASE.test(t)) return 'silent';
  return isEmoji(t) ? 'emoji' : null;
}

// Every whole phrase NO_REPLY_PHRASE accepts (spacing aside), for the prefix test below.
const PHRASES: string[] = [];
for (const open of ['(', '']) for (const w of ['reply', 'response']) for (const tail of ['', ' needed', ' required'])
  for (const close of [')', '']) for (const dot of ['.', '']) PHRASES.push(`${open}no ${w}${tail}${close}${dot}`);

/** Whether text still being written could yet become a quiet answer: nothing yet, a start of NO_REPLY, a start of a
 *  "no reply" phrase, or emoji alone so far (no letters, digits or spaces). */
export function couldBeQuiet(partial: string): boolean {
  const t = (partial ?? '').trim();
  if (!t) return true;
  if (NO_REPLY.startsWith(t) || quietAnswer(t)) return true;
  const n = t.toLowerCase().replace(/^\(\s+/, '(').replace(/\s+\)/, ')');
  if (PHRASES.some((p) => p.startsWith(n))) return true;
  return !/[\s\p{L}\p{N}]/u.test(t) && PICTOGRAPH.test(t);
}

/** The agent is answering a reaction wake: the newest user text in the chat is a wake turn (api reaction_wake). */
export function answeringWake(items: readonly object[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i] as { kind?: unknown; role?: unknown; reaction_wake?: unknown };
    if (it?.kind === 'text' && it.role === 'user') return it.reaction_wake === true;
  }
  return false;
}
