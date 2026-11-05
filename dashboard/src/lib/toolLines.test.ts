// Bubbles: a turn's tool run is ONE thin, quiet line between the bubbles ("Collapse to a thin line
// that's still expandable (but displays other thin lines which are themselves expandable) where applicable, and can
// expand to display a code div (which is always copyable)"). The kit's one ToolCard draws it (no parallel card), from
// the same fold (runSummary), density and Copy; Modern keeps its card. No DOM test runner here: wiring is read from
// the source, as callSkin.test.ts does.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('the kit ToolCard draws the thin lines when asked; the card stays the default', () => {
  const card = src('components/kit/ToolCard.tsx');
  assert.match(card, /look = 'card'/);
  assert.match(card, /if \(look === 'lines'\) return <ToolLines /);
  // the fold's own summary and the density setting (verbose starts the list open)
  const lines = card.slice(card.indexOf('function ToolLines'));
  assert.match(lines, /runSummary\(calls, interrupted\)/);
  assert.match(lines, /useState\(density === 'verbose'\)/);
  // one call skips the middle level: its line opens straight to its code block
  assert.match(lines, /if \(calls\.length === 1\) return <CallLine /);
});

test('every line is a button that says whether it is open; the open is smooth (200ms) and honours reduced motion', () => {
  const card = src('components/kit/ToolCard.tsx');
  const lines = card.slice(card.indexOf('function Reveal'));
  assert.match(lines, /<button type="button" aria-expanded=\{/);
  assert.match(lines, /duration-200/);
  assert.match(lines, /motion-reduce:transition-none/);
  assert.doesNotMatch(lines, /duration-(1\d\d|[3-9]\d\d)\b/);
});

test('a call opens to a code block whose Copy is always shown (the code block chrome of the kit Markdown)', () => {
  const md = src('components/kit/Markdown.tsx');
  assert.match(md, /export function CodeFrame\(/);
  assert.match(md, /<CodeFrame className="my-1\.5"/);   // fenced code uses the same frame
  const frame = md.slice(md.indexOf('export function CodeFrame'), md.indexOf('function CodeBlock'));
  assert.match(frame, /<Copy text=\{copyText\}/);
  assert.doesNotMatch(frame, /opacity-0/);
  const card = src('components/kit/ToolCard.tsx');
  assert.match(card, /<CodeFrame className="mt-0\.5 mb-1\.5" label=\{callLabel\(call\.name\)\} copyText=\{code\.copy\}/);
  assert.match(card, /callCode\(call\)/);
});

test("a run still going shows its live step on the line; every chat style asks for lines", () => {
  const card = src('components/kit/ToolCard.tsx');
  assert.match(card, /liveCall\(calls, interrupted\)/);
  assert.match(src('components/kit/ThreadView.tsx'), /<ToolCard key=\{i\} calls=\{p\.calls\} skin=\{skin\} look="lines" \/>/);
  const chat = src('components/chat/TranscriptChatView.tsx');
  assert.match(chat, /<GroupedNodeView node=\{node\} interrupted=\{interrupted\} look="lines" \/>/);
  const cards = src('components/chat/TranscriptCards.tsx');
  assert.match(cards, /<ToolCalls calls=\{calls\} entries=\{entries\} interrupted=\{interrupted\} density=\{density\} look=\{look\} \/>/);
  assert.match(cards, /<ToolCalls calls=\{\[fromBlock\(block\)\]\} density=\{density\} look=\{look\} \/>/);
});

test("the lines, the code block and the fenced code frame follow the app's light/dark setting (theme tokens only)", () => {
  // "expanded toolcalls need to also match the settings for light/dark". The theme is the .dark class
  // on <html> (index.css: :root light, .dark dark); the --bui-* tokens (ink, field, inset, line, red, orange) switch with it.
  const palette = /\b(?:text|bg|border|from|to)-(?:neutral|gray|zinc|slate|stone|amber|orange|red|blue|green)-\d{2,3}\b/;
  const md = src('components/kit/Markdown.tsx');
  const frame = md.slice(md.indexOf('export function CodeFrame'), md.indexOf('function renderInline'));
  assert.doesNotMatch(frame, palette);
  assert.match(frame, /bg-inset/);
  assert.match(frame, /border-line/);
  const card = src('components/kit/ToolCard.tsx');
  assert.doesNotMatch(card.slice(card.indexOf('function Reveal')), palette);
  // Arturo's pane is dark in either theme: its code keeps the dark tokens
  assert.match(md, /<CodeBlock key=\{key\} code=\{b\.code\} lang=\{b\.lang\} dark=\{skin === 'pane'\} \/>/);
  assert.match(frame, /\$\{dark \? 'dark ' : ''\}/);
});

test('times on the lines: the run its start (or range), each call its own; at the line end, outside the label, spoken naturally', () => {
  const card = src('components/kit/ToolCard.tsx');
  const lines = card.slice(card.indexOf('function Reveal'));
  assert.match(lines, /function LineTime\(/);
  assert.match(lines, /<span className="sr-only">\{end \? ', from ' : ', at '\}<\/span>/);
  assert.match(lines, /<span className="sr-only"> to <\/span>/);
  assert.match(lines, /time=\{<LineTime start=\{call\.ts\} \/>\}/);
  assert.match(lines, /const when = runTimes\(calls\);/);
  // the time is its own slot after the chevron, not inside the words the label truncates
  // right after the chevron (gap 6px + 2px), not pushed to the column's far edge; shrink-0 so only the words truncate
  assert.match(lines, /\{\(tail \|\| \(time && !wrap\)\) && <span className="ml-0\.5 flex shrink-0 items-center gap-2">\{tail\}\{!wrap && time\}<\/span>\}/);
  // the run's own line wraps: its time rides inline after the chevron
  assert.match(lines, /\{children\}\{chevron\}\{time && <span className="ml-2 inline-block">\{time\}<\/span>\}/);
  assert.doesNotMatch(lines, /ml-auto/);
});
