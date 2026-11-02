/**
 * EmojiPicker — the full picker behind "more" (components/kit/Reactions.tsx), on desktop and phone alike: a search
 * field, the nine categories (a tab jumps to one), skin tones (the last one chosen is kept in this browser), and the
 * name of the emoji under the pointer. Its data is bundled (lib/emojiData.gen.ts, scripts/emoji-data.mjs) into this
 * file's own chunk, loaded the first time "more" is opened: it works offline and never fetches anything.
 * Pinning favourites into the quick row is deferred (spec).
 */
import { useMemo, useRef, useState } from 'react';
import { EMOJI_GROUPS } from '../../lib/emojiData.gen';
import { readTone, searchEmoji, withSkinTone, writeTone, SKIN_TONES, type SkinTone } from '../../lib/emojiPicker';

/** Each category's tab. */
const TAB: Record<string, string> = {
  smileys_emotion: '😀', people_body: '👋', animals_nature: '🐻', food_drink: '🍔', travel_places: '🚗',
  activities: '⚽', objects: '💡', symbols: '❤️', flags: '🏁',
};

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export default function EmojiPicker({ onPick, autoFocus = true }: { onPick: (emoji: string) => void; autoFocus?: boolean }) {
  const [query, setQuery] = useState('');
  const [tone, setTone] = useState<SkinTone>(() => readTone());
  const [hover, setHover] = useState<{ emoji: string; name: string } | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => searchEmoji(EMOJI_GROUPS, query), [query]);
  const chooseTone = (t: SkinTone) => { setTone(t); writeTone(t); };
  const jump = (slug: string) => {
    body.current?.querySelector(`[data-group="${slug}"]`)?.scrollIntoView({ block: 'start', behavior: reduced() ? 'auto' : 'smooth' });
  };
  const cell = (emoji: string, name: string, tones: 0 | 1) => {
    const e = withSkinTone(emoji, tones, tone);
    return (
      <button key={emoji} type="button" className="rx-cell" aria-label={name} title={name}
        onPointerEnter={() => setHover({ emoji: e, name })} onFocus={() => setHover({ emoji: e, name })}
        onClick={() => onPick(e)}>{e}</button>
    );
  };
  return (
    <>
      <div className="rx-picker-head">
        <input className="rx-search" type="search" placeholder="Search emoji" aria-label="Search emoji" autoFocus={autoFocus}
          value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // preventDefault: the focus goes back to the React button on a pick, and this Enter must not press it too
            if (e.key === 'Enter' && hits[0]) { e.preventDefault(); onPick(withSkinTone(hits[0].emoji, hits[0].tones, tone)); }
          }} />
        <div className="rx-tones" role="group" aria-label="Skin tone">
          {SKIN_TONES.map((t) => (
            <button key={t || 'none'} type="button" aria-pressed={tone === t} aria-label={t ? `Skin tone ${SKIN_TONES.indexOf(t)}` : 'No skin tone'}
              onClick={() => chooseTone(t)}>{withSkinTone('✋', 1, t)}</button>
          ))}
        </div>
      </div>
      {!query && (
        <div className="rx-tabs" role="group" aria-label="Jump to a category">
          {EMOJI_GROUPS.map((g) => (
            <button key={g.slug} type="button" aria-label={g.name} title={g.name} onClick={() => jump(g.slug)}>{TAB[g.slug] ?? g.emojis[0]?.[0]}</button>
          ))}
        </div>
      )}
      <div ref={body} className="rx-picker-body">
        {query ? (
          hits.length ? <div className="rx-grid">{hits.map((h) => cell(h.emoji, h.name, h.tones))}</div>
            : <div className="rx-empty">No emoji match “{query}”</div>
        ) : EMOJI_GROUPS.map((g) => (
          <section key={g.slug} className="rx-group" data-group={g.slug} aria-label={g.name}>
            <div className="rx-group-name">{g.name}</div>
            <div className="rx-grid">{g.emojis.map(([e, name, tones]) => cell(e, name, tones))}</div>
          </section>
        ))}
      </div>
      <div className="rx-picker-foot" aria-hidden>
        {hover ? <><span className="rx-big">{hover.emoji}</span><span>{hover.name}</span></> : <span>Pick an emoji</span>}
      </div>
    </>
  );
}
