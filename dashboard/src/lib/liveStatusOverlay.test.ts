import { test } from 'node:test';
import assert from 'node:assert/strict';
import { liveWord, overlayLiveStatus, overlayAgentsPayload } from './liveStatusOverlay.ts';

const live = (status: string) => ({ status, at: 0 });

test("telemetryd's words map onto the dashboard's working/idle", () => {
  assert.equal(liveWord('streaming'), 'working');
  assert.equal(liveWord('computing'), 'working');
  assert.equal(liveWord('idle'), 'idle');
  for (const w of ['waiting_permission', 'stalled', 'offline_crashed', undefined]) assert.equal(liveWord(w), null);
});

test('a short turn the poll missed: detector idle, stream streaming -> working', () => {
  const [a] = overlayLiveStatus([{ id: 'pm-demo', status: 'idle' }], { 'pm-demo': live('streaming') });
  assert.equal(a.status, 'working');
});
test('the turn ended: detector still working from its cache, stream idle -> idle', () => {
  const [a] = overlayLiveStatus([{ id: 'pm-demo', status: 'working' }], { 'pm-demo': live('idle') });
  assert.equal(a.status, 'idle');
});
test('a menu, unsent text or a stopped seat is never replaced', () => {
  for (const status of ['waiting_permission', 'stranded_input', 'queued_input', 'stopped', 'retired']) {
    const [a] = overlayLiveStatus([{ id: 'pm-demo', status }], { 'pm-demo': live('idle') });
    assert.equal(a.status, status);
  }
});
test('a pending menu keeps the detector reading even if it says idle', () => {
  const [a] = overlayLiveStatus([{ id: 'pm-demo', status: 'idle', pending_menu: { options: [] } }], { 'pm-demo': live('streaming') });
  assert.equal(a.status, 'idle');
});
test('matched by tmux session when the id differs; no reading leaves it untouched', () => {
  const agents = [{ id: 'demo', tmux_session: 'demo-sess', status: 'idle' }, { id: 'other', status: 'idle' }];
  const out = overlayLiveStatus(agents, { 'demo-sess': live('computing') });
  assert.equal(out[0].status, 'working');
  assert.equal(out[1], agents[1]);
});
test('payload: no live readings returns the very same object (no re-render churn)', () => {
  const p = { agents: [{ id: 'x', status: 'idle' }] };
  assert.equal(overlayAgentsPayload(p, {}), p);
});
