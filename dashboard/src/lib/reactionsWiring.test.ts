// The reaction parts are wired where the spec puts them (reactions build step 2): the agent chat provides its
// reactions; ThreadView wraps a reactable row without adding an element of its own (scroll anchoring and the reply's
// hand-off read the column's children); the bubbles carry the badges; desktop or phone is the pointer, not the width;
// the hold is lib/railAgents longPress; the full picker is its own chunk. Components have no DOM test runner here, so
// their wiring is read from the source, as callSkin.test.ts does.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('the agent chat reads its reactions and provides them around the thread', () => {
  const chat = src('components/chat/TranscriptChatView.tsx');
  assert.match(chat, /useReactions\('agent-chat', agentId\)/);
  assert.match(chat, /agentRows\(entries, loading \? \[\] : pending, st, \{ modern, reactions: reactions\.map \}\)/);
  assert.match(chat, /<ReactionsContext\.Provider value=\{reactionsCtx\}>\s*<ThreadView /);
});

test('ThreadView wraps both node branches (Modern and the chat\'s own primitives); the old badge stays for rows without a ref', () => {
  const tv = src('components/kit/ThreadView.tsx');
  assert.equal((tv.match(/\{reactable\(r, /g) ?? []).length, 2);
  assert.match(tv, /<ReactionRow reactRef=\{r\.ref\}/);
  assert.match(tv, /\{r\.reaction && <Reaction emoji=\{r\.reaction\} \/>\}/);
  assert.match(tv, /<ReactionAnchor><ReplyWords /);
});

test('ReactionRow adds no element of its own: a context provider, its children, and menus in portals', () => {
  const rx = src('components/kit/Reactions.tsx');
  const row = rx.slice(rx.indexOf('export function ReactionRow'), rx.indexOf('async function copy'));
  assert.match(row, /return \(\s*<ReactionSlotContext\.Provider value=\{slot\}>\s*\{children\}/);
  assert.match(rx, /createPortal\(/);
  assert.match(rx, /longPressControl\(/);
  assert.match(rx, /from '\.\.\/\.\.\/lib\/railAgents'/);
  assert.match(rx, /const loadPicker = \(\) => import\('\.\/EmojiPicker'\);/);
  assert.match(rx, /let EmojiPicker = lazy\(loadPicker\);/);
});

test('every bubble of the agent chat carries the slot: the person\'s (both skins) and the agent\'s reply', () => {
  assert.match(src('components/kit/Turns.tsx'), /data-user-words="bubbles" \{\.\.\.anchorProps\(slot, 'user'\)\}/);
  assert.match(src('components/kit/Turns.tsx'), /data-user-words="modern" \{\.\.\.anchorProps\(slot, 'user'\)\}/);
  assert.match(src('components/chat/TranscriptCards.tsx'), /<div className=\{BUBBLES_USER\} \{\.\.\.anchorProps\(slot, 'user'\)\}>/);
  // the agent's reply carries the slot on its own bubble (MessageText `reactions`), not on a wrapper round its words
  const msg = src('components/kit/Message.tsx');
  assert.match(msg, /<MessageText text=\{text\} skin="bubbles" reactions reveal=\{reveal\} \/>/);
  assert.doesNotMatch(msg, /<ReactionAnchor>/);
  assert.match(msg, /\{\.\.\.anchorProps\(s, 'agent'\)\} data-rx-bubble=\{s \? '' : undefined\}/);
  assert.match(msg, /\{s && <><SlotBar side="agent" \/><SlotBadges side="agent" \/><\/>\}/);
});

test('an agent bubble is measured as itself (its border is real); only Modern\'s bare reply measures its words', () => {
  assert.match(src('components/kit/ReactionBadges.tsx'), /side === 'agent' && !anchor\.hasAttribute\('data-rx-bubble'\) \? wordsBox\(anchor\) : anchor\.getBoundingClientRect\(\)/);
  assert.match(src('components/kit/Reactions.tsx'), /side === 'agent' && !el\.hasAttribute\('data-rx-bubble'\) \? wordsBox\(el\) : boxOfEl\(el\)/);
  const css = src('components/kit/reactions.css');
  // the bubble gets the same 15px of room as the person's; the lift-off-the-words padding is only for Modern's bare reply
  assert.doesNotMatch(css, /\[data-rx-anchor="agent"\]\[data-has-badges\] \{/);
  assert.match(css, /\[data-rx-anchor="agent"\]:not\(\[data-rx-bubble\]\)\[data-has-badges\] \{ margin-top: 4px; padding-top: 11px; \}/);
  // the phone hold lifts the bubble as it is (no extra padding or background round it)
  const rx = src('components/kit/Reactions.tsx');
  assert.match(rx, /const bare = side === 'agent' && skin !== 'bubbles';/);
  assert.match(css, /\.rx-lifted > \[data-agent-bubble\] \{ max-width: none; \}/);
});

test('desktop or phone is (pointer: coarse), never a width; badges straddle the border at the inner corner', () => {
  assert.match(src('components/kit/reactionsContext.ts'), /'\(pointer: coarse\)'/);
  const css = src('components/kit/reactions.css');
  // desktop: centre line on the TOP border; phone: on the BOTTOM border (lib/reactionLayout badgePlace)
  assert.match(css, /@media \(pointer: fine\) \{\s*\.rx-badges \{ top: 0; transform: translateY\(-50%\); \}/);
  assert.match(css, /@media \(pointer: coarse\) \{\s*\.rx-badges \{ bottom: 0; transform: translateY\(50%\); \}/);
  // the inner corner (the side toward the middle), overhanging it by BADGE_OVERHANG
  assert.match(css, /\.rx-badges\[data-side="user"\] \{ left: -8px; \}/);
  assert.match(css, /\.rx-badges\[data-side="agent"\] \{ right: -8px; \}/);
  // room only on rows with badges (badgeReserve: 22 / 2 + 4)
  assert.match(css, /\[data-rx-anchor\]\[data-has-badges\] \{ margin-top: 15px; \}/);
  assert.match(css, /\[data-rx-anchor\]\[data-has-badges\] \{ margin-bottom: 15px; \}/);
  assert.match(src('components/kit/ReactionBadges.tsx'), /badgePlace\(side, coarse \? 'phone' : 'desktop'/);
  assert.match(src('components/kit/Reactions.tsx'), /barOffsetTop\(side, /);
  assert.doesNotMatch(css, /max-width:\s*\d+px\)/);
  assert.match(css, /prefers-reduced-motion: reduce/);
});

test('motion is slow and smooth: no animation or transition under 160ms, no overshooting curve', () => {
  const css = src('components/kit/reactions.css');
  for (const m of css.matchAll(/(\d+)ms/g)) assert.ok(Number(m[1]) >= 160, `${m[0]} is too quick`);
  for (const m of css.matchAll(/cubic-bezier\(([^)]+)\)/g)) {
    const [, y1, , y2] = m[1].split(',').map(Number);
    assert.ok(y1 <= 1 && y2 <= 1 && y1 >= 0 && y2 >= 0, `${m[0]} overshoots`);
  }
});

test('the full picker never fetches: no URL and no fetch in its code or its data (it works offline)', () => {
  for (const f of ['components/kit/EmojiPicker.tsx', 'lib/emojiPicker.ts', 'lib/emojiData.gen.ts']) {
    assert.doesNotMatch(src(f), /\bfetch\(|https?:\/\//, f);
  }
});

test('menus take the focus and give it back; the phone sheet is modal and traps it', () => {
  const rx = src('components/kit/Reactions.tsx');
  const pop = rx.slice(rx.indexOf('function Popover'), rx.indexOf('function LiftSheet'));
  assert.match(pop, /useEffect\(\(\) => \{ focusFirst\(ref\.current\); \}, \[\]\);/);
  const row = rx.slice(rx.indexOf('export function ReactionRow'), rx.indexOf('class PickerBoundary'));
  assert.match(row, /const close = useCallback\(\(\) => \{[\s\S]*?back\.focus\(\{ preventScroll: true \}\)[\s\S]*?\}, \[hold\]\);/);
  assert.match(row, /const pick = \(emoji: string\) => \{ close\(\);/);
  const lift = rx.slice(rx.indexOf('function LiftSheet'), rx.indexOf('export function ReactionNotice'));
  assert.match(lift, /role="dialog" aria-modal="true"/);
  assert.match(lift, /onKeyDown=\{trap\}/);
  assert.match(lift, /focusFirst\(/);
  assert.match(lift, /downOnDim\.current && performance\.now\(\) - openedAt\.current >= GHOST_MS/);
});

test('a failed picker chunk is caught inside the popover with a retry', () => {
  const rx = src('components/kit/Reactions.tsx');
  assert.match(rx, /class PickerBoundary extends Component/);
  assert.match(rx, /static getDerivedStateFromError\(\)/);
  assert.match(rx, /Couldn’t load emoji — try again/);
  assert.match(rx, /retry = \(\) => \{ EmojiPicker = lazy\(loadPicker\);/);
  // a retry draws the NEW lazy picker: the boundary hands its render prop the current one each time
  assert.match(rx, /<PickerBoundary render=\{\(Picker\) => \(\s*<Suspense /);
  assert.match(rx, /return this\.props\.render\(EmojiPicker\);/);
  assert.doesNotMatch(rx, /this\.props\.children/);
});

test('desktop popovers close on the person scrolling (wheel, swipe), not on the thread following a reply', () => {
  const rx = src('components/kit/Reactions.tsx');
  const pop = rx.slice(rx.indexOf('function Popover'), rx.indexOf('function LiftSheet'));
  assert.doesNotMatch(pop, /addEventListener\('scroll'/);
  assert.match(pop, /addEventListener\('wheel'/);
  assert.match(pop, /addEventListener\('touchmove'/);
});

test('a phone gets a hidden React button; the system long-press is held back only during a hold', () => {
  const badges = src('components/kit/ReactionBadges.tsx');
  assert.match(badges, /className="rx-sr-react" aria-haspopup="dialog"/);
  assert.match(badges, /aria-pressed=\{slot\.coarse \? undefined : g\.mine\}/);
  assert.doesNotMatch(badges, /w-fit/);           // Modern's bare reply keeps its own width (the bubble sizes itself)
  const css = src('components/kit/reactions.css');
  assert.match(css, /\[data-rx-anchor\]\[data-rx-holding\] \{ -webkit-touch-callout: none;/);
  assert.doesNotMatch(css, /\[data-rx-anchor\] \{ -webkit-touch-callout/);
  assert.match(src('components/kit/reactionsContext.ts'), /if \(e\.currentTarget\.hasAttribute\('data-rx-holding'\)\) e\.preventDefault\(\);/);
});

test('the picker: plain category buttons, a quiet footer, the jump honours reduced motion', () => {
  const p = src('components/kit/EmojiPicker.tsx');
  assert.doesNotMatch(p, /role="tab/);
  assert.doesNotMatch(p, /aria-live/);
  assert.match(p, /behavior: reduced\(\) \? 'auto' : 'smooth'/);
});

test('closing a menu gives the focus back only when it is still the menu\'s (or nowhere)', () => {
  const rx = src('components/kit/Reactions.tsx');
  const close = rx.slice(rx.indexOf('const close = useCallback'), rx.indexOf('}, [hold]);'));
  assert.match(close, /const ours = !active \|\| active === document\.body \|\| !!active\.closest\('\.rx-pop, \.rx-lift'\)/);
  assert.match(close, /if \(ours && back\?\.isConnected\) back\.focus/);
});

test('a scrolling key outside a desktop menu closes it; the hidden phone buttons carry no selectable text', () => {
  const rx = src('components/kit/Reactions.tsx');
  assert.match(rx, /SCROLL_KEYS\.has\(e\.key\) && !ref\.current\?\.contains/);
  const badges = src('components/kit/ReactionBadges.tsx');
  assert.match(badges, /data-label="React"/);
  assert.doesNotMatch(badges, />React<\/button>/);
  assert.match(src('components/kit/reactions.css'), /\.rx-sr-react::before \{ content: attr\(data-label\); \}/);
});
