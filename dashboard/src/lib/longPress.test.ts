// lib/railAgents longPress / longPressControl: the hold that opens the agent rail's menu and a message's reactions.
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { GHOST_CLICK_MS, longPress, longPressControl } from './railAgents.ts';

const at = (x: number, y: number) => ({ touches: [{ clientX: x, clientY: y }], currentTarget: {} });
const click = () => { const e = { prevented: false, preventDefault() { e.prevented = true; }, stopPropagation() {} }; return e; };

test('fires after the hold and swallows only the tap that lifts the finger', () => {
  mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  try {
    let fired = 0;
    const lp = longPress(() => { fired++; });
    lp.onTouchStart(at(10, 10));
    mock.timers.tick(499);
    assert.equal(fired, 0);
    mock.timers.tick(1);
    assert.equal(fired, 1);
    lp.onTouchEnd();
    const ghost = click();
    lp.onClickCapture(ghost);
    assert.equal(ghost.prevented, true);
    const next = click();
    lp.onClickCapture(next);
    assert.equal(next.prevented, false);
  } finally { mock.timers.reset(); }
});

test('a fired press does not swallow a later click that no tap preceded (a screen reader, a mouse)', () => {
  mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  try {
    const lp = longPress(() => {});
    lp.onTouchStart(at(0, 0));
    mock.timers.tick(500);
    lp.onTouchEnd();
    mock.timers.tick(GHOST_CLICK_MS + 1);
    const late = click();
    lp.onClickCapture(late);
    assert.equal(late.prevented, false);
  } finally { mock.timers.reset(); }
});

test('a move past 8px or an early lift cancels; onEnd says whether it fired; reset forgets a fired press', () => {
  mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  try {
    const ends: boolean[] = [];
    let fired = 0;
    const c = longPressControl(() => { fired++; }, 500, { onEnd: (f) => ends.push(f) });
    c.handlers.onTouchStart(at(0, 0));
    c.handlers.onTouchMove(at(0, 9));
    mock.timers.tick(600);
    assert.equal(fired, 0);
    c.handlers.onTouchStart(at(0, 0));
    mock.timers.tick(100);
    c.handlers.onTouchEnd();
    mock.timers.tick(600);
    assert.equal(fired, 0);
    c.handlers.onTouchStart(at(0, 0));
    mock.timers.tick(500);
    assert.equal(fired, 1);
    c.reset();
    c.handlers.onTouchEnd();
    const after = click();
    c.handlers.onClickCapture(after);
    assert.equal(after.prevented, false);
    assert.deepEqual(ends, [false, false, false]);
  } finally { mock.timers.reset(); }
});

test('the plain longPress keeps exactly the handlers AgentRail spreads on its row', () => {
  assert.deepEqual(Object.keys(longPress(() => {})).sort(), ['onClickCapture', 'onTouchCancel', 'onTouchEnd', 'onTouchMove', 'onTouchStart']);
});
