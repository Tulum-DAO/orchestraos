/**
 * P0c1 — re-injecting saved questionnaire answers cannot break out of the script element.
 * Imports the REAL module; no mirror.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { injectSavedAnswers, scriptSafeJson } from './questionnaireInject.js';

const PAGE = '<html><head><script>var x = 1;</script></head><body><form>q</form></body></html>';
const LS = String.fromCharCode(0x2028);
const PS = String.fromCharCode(0x2029);

/** What the browser's JS engine will see as `saved`, parsed back out of the emitted source. */
function savedOf(html: string): unknown {
  const m = html.match(/const saved = (.*);\n/);
  assert.ok(m, 'restore script not found');
  return JSON.parse(m[1]);
}

/** Closing script tags in the injected block. Exactly one is legitimate: its own closer. */
function closersInInjection(out: string): number {
  const block = out.slice(out.indexOf('<script>\n(function()'), out.lastIndexOf('</body>'));
  return (block.match(/<\/script/gi) || []).length;
}

test('HOLE 1: a </script> in an answer cannot end the block', () => {
  const answers = { q1: { value: 'a', notes: '</script><img src=x onerror=alert(1)>' } };
  const out = injectSavedAnswers(PAGE, answers);
  assert.equal(closersInInjection(out), 1, 'only the block\'s own closing tag may appear');
  assert.ok(!out.includes('<img src=x'), 'the payload must not appear as markup');
  assert.deepEqual(savedOf(out), answers, 'the data must round-trip unchanged');
});

test('HOLE 1: case and whitespace variants of the closer are covered by escaping "<"', () => {
  const answers = { q1: { notes: '</SCRIPT >  </ScRiPt\t>' } };
  const out = injectSavedAnswers(PAGE, answers);
  assert.equal(closersInInjection(out), 1);
  assert.deepEqual(savedOf(out), answers);
});

test('HOLE 2: `$\`` in an answer does NOT splice the document into the JSON', () => {
  // With a STRING replacement, `$\`` expands to everything before </body> — including the
  // page's own <script>…</script> — dropped into the middle of the escaped literal.
  const answers = { q1: { notes: 'before $` after' } };
  const out = injectSavedAnswers(PAGE, answers);
  assert.equal(closersInInjection(out), 1, 'the page\'s own </script> must not be spliced in');
  assert.ok(!out.slice(out.indexOf('const saved')).includes('var x = 1'),
    'the document prefix must not appear inside the restore script');
  assert.deepEqual(savedOf(out), answers);
});

test('HOLE 2: every replacement pattern is inserted literally', () => {
  for (const pat of ['$&', "$'", '$$', '$1', '$<name>']) {
    const answers = { q1: { notes: `x${pat}y` } };
    const out = injectSavedAnswers(PAGE, answers);
    assert.deepEqual(savedOf(out), answers, `pattern ${pat} was expanded`);
  }
});

test('U+2028 and U+2029 cannot split the statement', () => {
  const answers = { q1: { notes: `line${LS}sep${PS}para` } };
  const out = injectSavedAnswers(PAGE, answers);
  assert.ok(!out.includes(LS) && !out.includes(PS), 'a raw separator is a JS line terminator');
  assert.deepEqual(savedOf(out), answers);
});

test('scriptSafeJson round-trips every character it escapes', () => {
  const v = { s: `<>&${LS}${PS}"'\\/\n\t` };
  assert.deepEqual(JSON.parse(scriptSafeJson(v)), v);
});

test('the control: ordinary answers are restored and placed before </body>', () => {
  const answers = { q1: { value: 'yes' }, q2: { value: 'no', notes: 'fine' } };
  const out = injectSavedAnswers(PAGE, answers);
  assert.deepEqual(savedOf(out), answers);
  assert.ok(out.indexOf('const saved') < out.lastIndexOf('</body>'));
  assert.ok(out.endsWith('</body></html>'));
});

test('a page with no </body> is returned unchanged', () => {
  const page = '<div>fragment</div>';
  assert.equal(injectSavedAnswers(page, { q1: { notes: '</script>' } }), page);
});
