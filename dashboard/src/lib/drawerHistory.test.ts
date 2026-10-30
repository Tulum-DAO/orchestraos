import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  historyItems, groupHistory, fromDecision, fromBatch, outcomeOf, decisionAnswer, batchAnswerLines, batchAnswers,
  applyFilter, filterChips, pageOf, PAGE_SIZE, NO_FILTER, whenText, waitedText, waitingLine, resolvedLine, answerText,
  runningRows, historyKind, plainSummary,
} from './drawerHistory.ts';
import type { Decision } from './decisions.ts';
import type { BatchRecord } from './multiPart.ts';
import type { ArturoJob } from './arturoJobs.ts';

const d = (over: Partial<Decision>): Decision => ({
  id: 'req-1', from_agent: 'pm-demo', kind: 'menu', state: 'pending', question: 'Which sample layout?', summary: 'Two layouts are ready.',
  options: [], menu: { options: [{ n: '1', label: 'Layout A' }, { n: '2', label: 'Layout B' }, { n: '3', label: 'Other / Write-in...', input_kind: 'free_text' } as never] },
  risk_level: 'low', reversibility: 'easy', answer: null, answer_text: null, option_n: null, answered_by: null,
  answer_surface: null, answer_device: null, created_at: '2026-10-24T17:00:00Z', answered_at: null, expires_at: null, ...over,
});

const batch = (over: Partial<BatchRecord> = {}): BatchRecord => ({
  id: 'apr-batch-1', answered_at: '2026-10-25T09:00:00Z',
  parts: [
    { index: 0, question: 'Which colour?', select: 'single', options: [{ n: '1', label: 'Red' }, { n: '2', label: 'Blue' }, { n: '3', label: 'Type something.', input_kind: 'free_text' }] },
    { index: 1, question: 'Which sizes?', select: 'multi', options: [{ n: '1', label: 'Small' }, { n: '2', label: 'Large' }] },
    { index: 2, question: 'Anything else?', select: 'single', options: [{ n: '1', label: 'No' }, { n: '2', label: 'Type something.', input_kind: 'free_text' }] },
  ],
  answers: [{ part: 0, ns: ['2'], text: 'navy if you have it' }, { part: 1, skipped: true }, { part: 2, ns: ['2'], text: 'Ship it Friday' }],
  ...over,
});

test('kinds: a question card is a decision, Approve/Deny an approval, a terminal menu record a menu', () => {
  assert.equal(historyKind({ kind: 'menu' }), 'decision');
  assert.equal(historyKind({ kind: 'deploy' }), 'approval');
  assert.equal(historyKind({ kind: null }), 'approval');
  assert.equal(historyKind({ kind: 'menu_answer' }), 'menu');
  assert.equal(fromBatch(batch(), 'pm-demo').kind, 'multipart');
});

test('outcome: from the feed state, approve/deny and held delivery read like the chat card', () => {
  assert.equal(outcomeOf(d({})), null);
  assert.equal(outcomeOf(d({ state: 'answered', answer: 'option', option_n: '1', delivery: 'delivered' })), 'answered');
  assert.equal(outcomeOf(d({ state: 'answered', answer: 'option', option_n: '1', delivery: 'held' })), 'held');
  assert.equal(outcomeOf(d({ kind: 'deploy', state: 'answered', answer: 'approve' })), 'approved');
  assert.equal(outcomeOf(d({ kind: 'deploy', state: 'answered', answer: 'deny' })), 'denied');
  assert.equal(outcomeOf(d({ state: 'dismissed' })), 'dismissed');
  assert.equal(outcomeOf(d({ state: 'expired' })), 'expired');
  assert.equal(outcomeOf(d({ state: 'withdrawn' })), 'withdrawn');
  assert.equal(outcomeOf(d({ state: 'superseded' })), 'replaced');
  assert.equal(outcomeOf(d({ kind: 'menu_answer', state: 'answered', answer: 'dismiss' })), 'dismissed');
  assert.equal(outcomeOf(d({ kind: 'menu_answer', state: 'answered', answer: 'answer' })), 'answered');
});

test('the answer of a decision: the pick, the words, or both; a write-in pick is the words alone', () => {
  assert.deepEqual(decisionAnswer(d({ state: 'answered', answer: 'option', option_n: '2' })), [{ text: 'Layout B', skipped: false }]);
  assert.deepEqual(decisionAnswer(d({ state: 'answered', answer: 'option', option_n: '1', answer_text: 'with the wide header' })),
    [{ text: 'Layout A — with the wide header', skipped: false }]);
  assert.deepEqual(decisionAnswer(d({ state: 'answered', answer: 'option', option_n: '3', answer_text: 'Neither, try a third' })),
    [{ text: 'Neither, try a third', skipped: false }]);
  // Nothing was answered: the badge says how it ended, there is no answer line.
  assert.deepEqual(decisionAnswer(d({ state: 'dismissed' })), []);
  assert.deepEqual(decisionAnswer(d({ state: 'expired' })), []);
  assert.deepEqual(decisionAnswer(d({})), []);
});

test('the answer of an approval: the verb, and a deny carries its reason', () => {
  assert.deepEqual(decisionAnswer(d({ kind: 'deploy', state: 'answered', answer: 'approve' })), [{ text: 'Approved', skipped: false }]);
  assert.deepEqual(decisionAnswer(d({ kind: 'deploy', state: 'answered', answer: 'deny', answer_text: 'Wait for the review' })),
    [{ text: 'Denied — Wait for the review', skipped: false }]);
});

test('the answer of a terminal menu record: the option label, words, or the bare number', () => {
  const m = (over: Partial<Decision>) => d({ kind: 'menu_answer', state: 'answered', answer: 'answer', summary: null, ...over });
  assert.deepEqual(decisionAnswer(m({ option_n: '2', menu: { options: [{ n: '2', label: 'Yes, and allow' }] } })), [{ text: 'Yes, and allow', skipped: false }]);
  assert.deepEqual(decisionAnswer(m({ option_n: '3', menu: { options: [{ n: '3', label: 'Type something.' }] }, answer_text: 'use the sample' })),
    [{ text: 'use the sample', skipped: false }]);
  assert.deepEqual(decisionAnswer(m({ option_n: '4', menu: null })), [{ text: 'Option 4', skipped: false }]);
  assert.deepEqual(decisionAnswer(m({ answer: 'dismiss' })), []);
});

test('a multi-part answer: one line per part with its question; skips say skipped; the write-in row is no pick', () => {
  assert.deepEqual(batchAnswerLines(batch()), [
    { question: 'Which colour?', text: 'Blue — navy if you have it', skipped: false },
    { question: 'Which sizes?', text: 'Skipped', skipped: true },
    { question: 'Anything else?', text: 'Ship it Friday', skipped: false },
  ]);
  // A multi-select part keeps every tick, in the menu's order; a part with no record entry is a skip.
  const b = batch({ answers: [{ part: 1, ns: ['2', '1'] }] });
  assert.deepEqual(batchAnswerLines(b).map((l) => l.text), ['Skipped', 'Small, Large', 'Skipped']);
  assert.deepEqual(batchAnswers(b)[1], { ns: ['1', '2'], text: '', skipped: false });
  assert.equal(answerText(batchAnswerLines(batch())), 'Which colour?: Blue — navy if you have it; Which sizes?: Skipped; Anything else?: Ship it Friday');
});

test('a multi-part card is titled by its first question and how many more', () => {
  const it = fromBatch(batch(), 'pm-demo');
  assert.equal(it.question, 'Which colour? (+2 more)');
  assert.equal(it.state, 'resolved');
  assert.equal(it.inChat, false);
  assert.equal(fromBatch(batch({ parts: batch().parts.slice(0, 1) }), 'pm-demo').question, 'Which colour?');
});

test('who and where: you for the operator, the agent that answered its own gate, nobody when none did', () => {
  const a = fromDecision(d({ state: 'answered', answer: 'option', option_n: '1', answered_by: 'operator', answer_surface: 'web',
    answered_at: '2026-10-24T17:05:00Z', resumed_at: '2026-10-24T17:05:02Z', delivery: 'delivered' }));
  assert.equal(a.by, 'you');
  assert.equal(a.where, 'the dashboard');
  assert.equal(a.delivery, 'delivered in 2s');
  assert.equal(fromDecision(d({ state: 'answered', answer: 'option', option_n: '1', answered_by: 'dev-demo' })).by, 'dev-demo');
  assert.equal(fromDecision(d({ state: 'expired' })).by, null);
  assert.equal(fromDecision(d({ state: 'dismissed' })).by, 'you');
});

test('grouping: waiting oldest first, resolved newest answered first', () => {
  const items = historyItems([
    d({ id: 'w-new', created_at: '2026-10-24T18:00:00Z' }),
    d({ id: 'r-old', state: 'answered', answer: 'option', option_n: '1', created_at: '2026-10-20T10:00:00Z', answered_at: '2026-10-20T11:00:00Z' }),
    d({ id: 'w-old', created_at: '2026-10-24T09:00:00Z' }),
    d({ id: 'r-new', state: 'dismissed', created_at: '2026-10-19T10:00:00Z', answered_at: '2026-10-26T08:00:00Z' }),
  ], [batch()], 'pm-demo');
  const g = groupHistory(items);
  assert.deepEqual(g.waiting.map((i) => i.id), ['w-old', 'w-new']);
  assert.deepEqual(g.resolved.map((i) => i.id), ['r-new', 'apr-batch-1', 'r-old']);
});

test('the same card from both feeds is shown once', () => {
  const items = historyItems([d({ id: 'apr-batch-1', state: 'answered', answer: 'batch' })], [batch()], 'pm-demo');
  assert.equal(items.length, 1);
});

test('filters by kind and state, and chips only for the kinds there are, with counts', () => {
  const items = historyItems([
    d({ id: 'a' }), d({ id: 'b', state: 'answered', answer: 'option', option_n: '1' }),
    d({ id: 'c', kind: 'deploy', state: 'answered', answer: 'approve' }),
  ], [batch()], 'pm-demo');
  assert.deepEqual(applyFilter(items, NO_FILTER).map((i) => i.id), ['a', 'b', 'c', 'apr-batch-1']);
  assert.deepEqual(applyFilter(items, { kind: 'decision', state: 'all' }).map((i) => i.id), ['a', 'b']);
  assert.deepEqual(applyFilter(items, { kind: 'decision', state: 'resolved' }).map((i) => i.id), ['b']);
  assert.deepEqual(applyFilter(items, { kind: 'all', state: 'waiting' }).map((i) => i.id), ['a']);
  const chips = filterChips(items, NO_FILTER);
  assert.deepEqual(chips.kinds.map((c) => [c.id, c.count]), [['all', 4], ['decision', 2], ['approval', 1], ['multipart', 1]]);
  assert.deepEqual(chips.states.map((c) => [c.id, c.count]), [['all', 4], ['waiting', 1], ['resolved', 3]]);
  // Counts follow the other filter; the chosen kind stays even when the state leaves it empty.
  const waitingApprovals = filterChips(items, { kind: 'approval', state: 'waiting' });
  assert.deepEqual(waitingApprovals.kinds.map((c) => [c.id, c.count]), [['all', 1], ['decision', 1], ['approval', 0]]);
  assert.deepEqual(waitingApprovals.states.map((c) => [c.id, c.count]), [['all', 1], ['waiting', 0], ['resolved', 1]]);
  // A single kind gets no kind row; the state row stays.
  const one = filterChips(items.filter((i) => i.kind === 'decision'), NO_FILTER);
  assert.deepEqual(one.kinds, []);
  assert.deepEqual(one.states.map((c) => c.count), [2, 1, 1]);
  assert.equal(chips.kinds[0].label, 'All kinds');
});

test('paging: 20 at a time, Show more brings at most a page', () => {
  const xs = Array.from({ length: 45 }, (_, i) => i);
  assert.equal(PAGE_SIZE, 20);
  assert.deepEqual(pageOf(xs, 20).shown.length, 20);
  assert.deepEqual([pageOf(xs, 20).more, pageOf(xs, 20).rest], [20, 25]);
  assert.deepEqual([pageOf(xs, 40).more, pageOf(xs, 40).rest], [5, 5]);
  assert.deepEqual([pageOf(xs, 60).shown.length, pageOf(xs, 60).more], [45, 0]);
  assert.deepEqual(pageOf([], 20), { shown: [], more: 0, rest: 0 });
});

test('when: time today, Yesterday, the date this year, the year before', () => {
  const now = new Date(2026, 9, 30, 15, 0).getTime();
  assert.equal(whenText(new Date(2026, 9, 30, 9, 5).getTime(), now), '09:05');
  assert.equal(whenText(new Date(2026, 9, 29, 23, 40).getTime(), now), 'Yesterday 23:40');
  assert.equal(whenText(new Date(2026, 9, 28, 20, 0).getTime(), now), 'Oct 28, 20:00');
  assert.equal(whenText(new Date(2025, 11, 3, 8, 0).getTime(), now), 'Dec 3 2025');
  assert.equal(whenText(null, now), '');
});

test('the waiting and resolved lines', () => {
  const now = Date.parse('2026-10-24T20:00:00Z');
  assert.equal(waitedText(now - 20_000, now), 'just now');
  assert.equal(waitedText(now - 12 * 60_000, now), '12m');
  assert.equal(waitedText(now - 3 * 3600_000, now), '3h');
  assert.equal(waitedText(now - 50 * 3600_000, now), '2d');
  assert.equal(waitingLine(fromDecision(d({ created_at: '2026-10-24T17:00:00Z' })), now), 'pm-demo · waiting 3h');
  const r = fromDecision(d({ state: 'answered', answer: 'option', option_n: '1', answered_by: 'operator', answer_surface: 'phone',
    answered_at: '2026-10-24T19:30:00Z', delivery: 'held' }));
  assert.match(resolvedLine(r, now), /^you · the phone app · \d\d:\d\d · delivery held$/);
  assert.match(resolvedLine(fromDecision(d({ state: 'expired', answered_at: null })), now), /^\w{3} \d+, \d\d:\d\d$|^\d\d:\d\d$/);
});

test('running: Arturo’s running jobs can be cancelled; an agent’s subagents are counted, with no cancel', () => {
  const job = (id: string, status: ArturoJob['status']): ArturoJob => ({ id, thread: 't', kind: 'research', title: `Sample ${id}`, status,
    started: 0, ended: null, steps: [], result: null, error: null });
  assert.deepEqual(runningRows({ jobs: [job('j1', 'running'), job('j2', 'done')], name: 'Example', elapsed: () => '0:42' }),
    [{ id: 'j1', title: 'Sample j1', detail: '0:42', cancellable: true }]);
  assert.deepEqual(runningRows({ subagents: 2, name: 'pm-demo' }), [{ id: 'subagents', title: '2 subagents working', detail: 'for pm-demo', cancellable: false }]);
  assert.deepEqual(runningRows({ subagents: 1, name: 'pm-demo' })[0].title, '1 subagent working');
  assert.deepEqual(runningRows({ subagents: 0, name: 'pm-demo' }), []);
  assert.deepEqual(runningRows({ jobs: [], name: 'Example' }), []);
});

test('the two-line preview is plain words', () => {
  assert.equal(plainSummary('**File:** `notes/sample.md`\n\n- one\n- two\n\n## Next\nSee [the doc](https://example.com).'),
    'File: notes/sample.md one two Next See the doc.');
  assert.equal(plainSummary('Before\n```\ncode\n```\nafter'), 'Before after');
  assert.equal(plainSummary(null), '');
});
