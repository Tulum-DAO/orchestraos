import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ruleToForm, formToRule, ruleSentence, actionChoices, human, newAction, ruleBody, isFormError, repeatChoices, EMPTY_FORM,
  PART1_DEFAULT_UNANSWERED, ACTS_FOR_YOU_NOTE, type RuleForm, type TimeoutRule,
} from './rulesPage.ts';
import { parseRules } from './supervision.ts';

const FORM: RuleForm = { ...EMPTY_FORM };

test('rule form round-trips and reads as a sentence', () => {
  const r: TimeoutRule = { trigger: 'unanswered', after_s: 3600, scope_agent: null, scope_kind: 'decision', scope_risk: null,
    actions: [{ type: 'remind', repeat_every_s: 1800 }, { type: 'choose_for_me' }], enabled: 1 };
  assert.deepEqual(formToRule(ruleToForm(r)), r);
  assert.equal(ruleSentence(r), 'When a decision waits 1 h: remind me every 30 min, then choose the recommended option for me');
});

test('the seeded part-1 default reads as it is', () => {
  assert.equal(ruleSentence(PART1_DEFAULT_UNANSWERED), 'When a question waits 1 h: remind me');
  const progress: TimeoutRule = { trigger: 'no_progress', after_s: 1200, scope_agent: null, scope_kind: null, scope_risk: null,
    actions: [{ type: 'remind' }], enabled: 0 };
  assert.equal(ruleSentence(progress), 'When an agent makes no progress for 20 min: remind me');
});

test('every action reads in plain words', () => {
  const base = { trigger: 'unanswered' as const, after_s: 7200, scope_agent: 'dev', scope_kind: null, scope_risk: 'low' as const, enabled: 1 };
  assert.equal(ruleSentence({ ...base, actions: [{ type: 'hand_off', to: 'supervisor' }] }),
    "When a low-risk question from dev waits 2 h: hand it to the agent's supervisor");
  assert.equal(ruleSentence({ ...base, actions: [{ type: 'hand_off', to: 'qa' }, { type: 'expire', mode: 'no_answer' }] }),
    'When a low-risk question from dev waits 2 h: hand it to qa, then close it with no answer');
  assert.equal(ruleSentence({ ...base, scope_risk: null, actions: [{ type: 'choose_for_me', by: 'supervisor' }, { type: 'mark_stuck' }, { type: 'expire' }] }),
    "When a question from dev waits 2 h: have its supervisor choose the recommended option for me, then mark it as stuck, then take the agent's default answer");
  assert.equal(ruleSentence({ ...base, scope_risk: null, scope_kind: 'human_task', actions: [{ type: 'remind' }] }),
    'When a task for you from dev waits 2 h: remind me');
});

test('human durations match the server', () => {
  assert.deepEqual([60, 600, 3600, 5400, 86400, 90000, 7 * 86400].map(human), ['1 min', '10 min', '1 h', '1 h 30 min', '1 d', '1 d 1 h', '7 d']);
});

test('actions not available show why and are disabled; acting-for-you actions carry the safety note', () => {
  const c = actionChoices([{ type: 'hand_off', available: false, why: 'x' }, { type: 'remind', available: true }, { type: 'choose_for_me', available: true }]);
  assert.equal(c[0].disabled, true);
  assert.equal(c[0].why, 'x');
  assert.equal(c[1].disabled, false);
  assert.equal(c[1].note, null);
  assert.equal(c[2].note, ACTS_FOR_YOU_NOTE);
  assert.equal(ACTS_FOR_YOU_NOTE, 'Never used on permission cards or high-risk questions.');
});

test('min timeframe honoured for no_progress', () => {
  const r = formToRule({ ...FORM, trigger: 'no_progress', minutes: 5 }, { min_after_s: 600 });
  assert.ok(isFormError(r));
  assert.equal(r.error, '10 min or more');
  assert.ok(!isFormError(formToRule({ ...FORM, trigger: 'no_progress', minutes: 10 }, { min_after_s: 600 })));
});

test('the form refuses what the server would', () => {
  const err = (f: Partial<RuleForm>) => { const r = formToRule({ ...FORM, ...f }); return isFormError(r) ? r.error : null; };
  assert.equal(err({ minutes: 0 }), 'A number of minutes, please');
  assert.equal(err({ minutes: Number.NaN }), 'A number of minutes, please');
  assert.equal(err({ minutes: 7 * 24 * 60 + 1 }), '7 days or less');
  assert.equal(err({ actions: [] }), 'Pick at least one thing to do');
  assert.equal(err({ kind: 'permission', actions: [newAction('hand_off')] }), ACTS_FOR_YOU_NOTE);
  assert.equal(err({ risk: 'high', actions: [newAction('choose_for_me')] }), ACTS_FOR_YOU_NOTE);
  assert.equal(err({ kind: 'permission', actions: [newAction('remind')] }), null);                    // a reminder is fine
  assert.equal(err({ actions: [{ type: 'remind', repeat_every_s: 120 }] }), 'Repeat every 5 min or more');
  assert.equal(err({ trigger: 'no_progress', minutes: 20, actions: [newAction('hand_off')] }), 'Hand it to another agent needs a question to act on');
  assert.equal(err({ trigger: 'no_progress', minutes: 20, kind: 'decision' }), 'A no-progress rule picks an agent only');
});

test('new actions start from the safe defaults, and the body sends enabled as a boolean', () => {
  assert.deepEqual(newAction('hand_off'), { type: 'hand_off', to: 'supervisor' });
  assert.deepEqual(newAction('expire'), { type: 'expire', mode: 'default' });
  assert.deepEqual(newAction('choose_for_me'), { type: 'choose_for_me', by: 'self' });
  const r = formToRule({ ...FORM, agent: ' dev ', enabled: false });
  assert.ok(!isFormError(r));
  assert.deepEqual(ruleBody(r), { trigger: 'unanswered', after_s: 3600, scope_agent: 'dev', scope_kind: null, scope_risk: null,
    actions: [{ type: 'remind' }], enabled: false });
});

test('parseRules reads the server body and refuses a bad one', () => {
  const ok = parseRules({ ok: true, person: 'p', armed: true, min_after_s: { no_progress: 600, unanswered: 60 },
    available_actions: [{ type: 'remind', available: true, why: null }],
    rules: [{ id: 'rul_1', trigger: 'unanswered', after_s: 3600, scope_agent: null, scope_kind: null, scope_risk: null,
      actions: [{ type: 'remind' }], enabled: 1, is_default: 1 }, { nope: true }] });
  assert.ok(ok.ok);
  if (ok.ok) {
    assert.equal(ok.value.rules.length, 1);
    assert.equal(ok.value.min_after_s.no_progress, 600);
    assert.equal(ok.value.armed, true);
  }
  assert.equal(parseRules({ ok: false, error: 'not_armed' }).ok, false);
});

test('any valid server rule round-trips exactly (seconds and odd repeats too)', () => {
  const r: TimeoutRule = { trigger: 'unanswered', after_s: 90, scope_agent: 'dev', scope_kind: null, scope_risk: 'low',
    actions: [{ type: 'remind', repeat_every_s: 420 }], enabled: 0 };
  assert.deepEqual(formToRule(ruleToForm(r)), r);
  assert.deepEqual(repeatChoices(420), [0, 7, 15, 30, 60, 120, 240]);
  assert.deepEqual(repeatChoices(undefined), [0, 15, 30, 60, 120, 240]);
});
