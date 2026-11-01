import { test } from 'node:test';
import assert from 'node:assert/strict';
import { closeNotice } from './terminalClose.ts';

function clock() {
  let t = 0; const jobs: { at: number; f: () => void; id: number }[] = []; let n = 0;
  return {
    schedule: (f: () => void, ms: number) => { const id = ++n; jobs.push({ at: t + ms, f, id }); return id; },
    cancel: (id: unknown) => { const i = jobs.findIndex((j) => j.id === id); if (i >= 0) jobs.splice(i, 1); },
    advance: (ms: number) => { t += ms; for (const j of jobs.filter((x) => x.at <= t)) { jobs.splice(jobs.indexOf(j), 1); j.f(); } },
  };
}

test('a real drop is still said, once the grace has passed', () => {
  const c = clock(); const said: string[] = [];
  const n = closeNotice((x) => said.push(x), { graceMs: 3000, ...c });
  n.error(); n.closed(); n.closed();
  c.advance(2999);
  assert.deepEqual(said, []);
  c.advance(1);
  assert.equal(said.length, 2);
  assert.ok(said[0].includes('[Connection error]') && said[1].includes('[Disconnected]'));
});

test('Back to chat (the page expects the close): nothing is said, before or after', () => {
  const c = clock(); const said: string[] = [];
  const n = closeNotice((x) => said.push(x), { graceMs: 3000, ...c });
  n.error(); n.closed();                 // the seat's terminal program ended before the page moved on
  n.expect();                            // ... and the page says it asked for that
  n.closed();
  c.advance(10_000);
  assert.deepEqual(said, []);
});

test('left from inside the terminal: the page follows within the grace and the terminal goes away, silently', () => {
  const c = clock(); const said: string[] = [];
  const n = closeNotice((x) => said.push(x), { graceMs: 3000, ...c });
  n.closed();
  c.advance(2000);
  n.dispose();
  c.advance(5000);
  assert.deepEqual(said, []);
});
