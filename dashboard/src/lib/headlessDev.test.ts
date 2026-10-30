// Dev for a headless seat (lib/headlessDev.ts): switch on choice when idle, wait for the turn on a page that opens
// in Dev, ask when Dev is chosen mid-turn, "Starting…" on the way back, the plain refusals with their choices, and a
// tmux seat exactly as before.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  INITIAL, afterSwitch, bannerFor, chooseStop, chooseWait, isBusy, retry, step, styleChanged, viewOf, DEV_NOTE,
  type DevInputs, type DevMemory,
} from './headlessDev.ts';

const hl = (o: Partial<DevInputs> = {}): DevInputs => ({ headless: true, actualDev: false, wantDev: true, busy: false, ...o });
const chosen: DevMemory = styleChanged(INITIAL);

test('a tmux seat: the chosen view, nothing sent, nothing said', () => {
  for (const headless of [false, null]) {
    const inp = { headless, actualDev: false, wantDev: true, busy: true };
    assert.equal(viewOf(inp), 'dev');
    assert.deepEqual(step(inp, chosen).action, 'none');
    assert.equal(viewOf({ ...inp, wantDev: false }), 'chat');
  }
});

test('Dev chosen on an idle seat switches at once and shows the chat until the terminal is open', () => {
  const { mem, action } = step(hl(), chosen);
  assert.equal(action, 'switchOn');
  assert.equal(mem.phase, 'switching');
  assert.equal(bannerFor(mem, 'Sample')!.text, 'Switching to the terminal…');
  assert.equal(viewOf(hl()), 'chat');
  assert.equal(step(hl(), mem).action, 'none');                       // in flight: never twice
  assert.equal(viewOf(hl({ actualDev: true })), 'dev');
  assert.equal(step(hl({ actualDev: true }), afterSwitch(mem, true, { status: 200, body: { ok: true } }).mem).mem.phase, 'none');
});

test('a page that opens in Dev mid-turn waits for the turn, never interrupts, then switches', () => {
  const waiting = step(hl({ busy: true }), INITIAL);
  assert.equal(waiting.action, 'none');
  assert.equal(waiting.mem.phase, 'waitTurn');
  assert.equal(bannerFor(waiting.mem, 'Sample')!.text, 'Switches to the terminal when this turn ends');
  assert.deepEqual(bannerFor(waiting.mem, 'Sample')!.actions, []);
  const done = step(hl({ busy: false }), waiting.mem);
  assert.equal(done.action, 'switchOn');
})

test('Dev chosen mid-turn asks, with Switch when it finishes first', () => {
  const { mem, action } = step(hl({ busy: true }), chosen);
  assert.equal(action, 'none');
  const b = bannerFor(mem, 'dev-demo')!;
  assert.equal(b.text, 'dev-demo is working. Switching now would stop this turn.');
  assert.deepEqual(b.actions.map((a) => a.label), ['Switch when it finishes', 'Stop and switch now']);
  // Switch when it finishes: the wait banner, then the switch at the turn's end.
  const w = chooseWait(mem);
  assert.equal(step(hl({ busy: true }), w).mem.phase, 'waitTurn');
  assert.equal(step(hl({ busy: false }), w).action, 'switchOn');
});

test('Stop and switch now: nothing is sent until the turn has stopped, then the switch', () => {
  const s = chooseStop(step(hl({ busy: true }), chosen).mem);
  assert.equal(bannerFor(s, 'Sample')!.text, 'Stopping this turn, then switching to the terminal…');
  assert.equal(step(hl({ busy: true }), s).action, 'none');
  assert.equal(step(hl({ busy: false }), s).action, 'switchOn');
});

test('a switch refused because a turn started in between asks again (or waits, on page open)', () => {
  assert.equal(afterSwitch(chosen, true, { status: 409, body: { ok: false, reason: 'busy' } }).mem.phase, 'askBusy');
  assert.equal(afterSwitch(INITIAL, true, { status: 409, body: { ok: false, reason: 'busy' } }).mem.phase, 'waitTurn');
});

test('Back to chat chosen: "Starting…" while it comes back; a busy terminal says so with Try again', () => {
  const inp = hl({ wantDev: false, actualDev: true });
  const { mem, action } = step(inp, chosen);
  assert.equal(action, 'switchOff');
  assert.equal(bannerFor(mem, 'Sample')!.text, 'Starting…');
  assert.equal(viewOf(inp), 'chat');
  const refused = afterSwitch(mem, false, { status: 409, body: { ok: false, busy: true } as never }).mem;
  const b = bannerFor(refused, 'Sample')!;
  assert.equal(b.text, 'The terminal is in use.');
  assert.deepEqual(b.actions.map((a) => a.label), ['Try again']);
  assert.equal(step(inp, refused).action, 'none');                     // no retry on its own
  assert.equal(step(inp, retry(refused)).action, 'switchOff');         // Try again
});

test('a page that opens in chat while the terminal is open leaves it open', () => {
  const r = step(hl({ wantDev: false, actualDev: true }), INITIAL);
  assert.equal(r.action, 'none');
  assert.equal(bannerFor(r.mem, 'Sample'), null);
});

test('other failures say what, plainly, with Try again; a seat that is not headless after all goes quiet', () => {
  const down = afterSwitch(chosen, true, { status: 503, body: { ok: false } }).mem;
  assert.equal(bannerFor(down, 'Sample')!.text, "The agent's process isn't running.");
  assert.equal(step(hl(), down).action, 'none');
  assert.equal(step(hl(), retry(down)).action, 'switchOn');
  assert.equal(bannerFor(afterSwitch(chosen, true, { status: 0, body: null }).mem, 'Sample')!.text, "Couldn't reach the server.");
  assert.equal(afterSwitch(chosen, true, { status: 404, body: { ok: false } }).headless, false);
});

test('changing your mind mid-way: choosing chat while waiting for the turn simply stops waiting', () => {
  const waiting = step(hl({ busy: true }), INITIAL).mem;
  const back = styleChanged(waiting);
  const r = step(hl({ wantDev: false, busy: true }), back);
  assert.equal(r.action, 'none');
  assert.equal(r.mem.phase, 'none');
});

test('busy states and the note', () => {
  for (const s of ['working', 'thinking', 'waiting_permission', 'waiting', 'stalled']) assert.equal(isBusy(s), true);
  for (const s of ['idle', 'stopped', undefined]) assert.equal(isBusy(s), false);
  assert.equal(DEV_NOTE, 'Uses the terminal. Switching back takes a few seconds.');
});
