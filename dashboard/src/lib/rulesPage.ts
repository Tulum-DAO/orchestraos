/**
 * rulesPage.ts — pure helpers for Settings › Timeout rules (attention part 2b): a rule ↔ its edit form, the sentence
 * a rule reads as ("When a decision waits 1 h: remind me every 30 min, then choose the recommended option for me"),
 * and which actions can be picked. The rules themselves live on the server (GET/POST/PUT/DELETE /api/attention/rules,
 * lib/supervision.ts); the server validates again and its words win on a refusal.
 */
export type Trigger = 'unanswered' | 'no_progress';
export type ScopeKind = 'decision' | 'questionnaire' | 'permission' | 'human_task';
export type ScopeRisk = 'low' | 'medium' | 'high';
export type ActionType = 'remind' | 'mark_stuck' | 'expire' | 'hand_off' | 'choose_for_me';

export interface RuleAction {
  type: ActionType | string;
  repeat_every_s?: number;
  mode?: 'default' | 'no_answer';
  to?: string;
  by?: 'self' | 'supervisor';
  answer_within_s?: number;
}
export interface TimeoutRule {
  id?: string; name?: string | null;
  trigger: Trigger; after_s: number;
  scope_agent: string | null; scope_kind: ScopeKind | null; scope_risk: ScopeRisk | null;
  actions: RuleAction[]; enabled: number | boolean;
  is_default?: number | boolean;
}
export interface AvailableAction { type: string; available: boolean; why?: string | null }

/** The form: minutes instead of seconds (fractional when the rule has seconds), '' for "any". */
export interface RuleForm {
  trigger: Trigger;
  minutes: number;
  agent: string;
  kind: ScopeKind | '';
  risk: ScopeRisk | '';
  actions: RuleAction[];
  enabled: boolean;
}

export const PART1_DEFAULT_UNANSWERED: TimeoutRule = {
  trigger: 'unanswered', after_s: 3600, scope_agent: null, scope_kind: null, scope_risk: null,
  actions: [{ type: 'remind' }], enabled: 1,
};

/** Shown under hand off, expire and choose for me (the server's safety gate, plan §8.2). */
export const ACTS_FOR_YOU_NOTE = 'Never used on permission cards or high-risk questions.';
export const ACTS_FOR_YOU: readonly string[] = ['expire', 'hand_off', 'choose_for_me'];
export const MIN_REPEAT_MIN = 5;
export const MAX_MINUTES = 7 * 24 * 60;

const ACTION_LABELS: Record<string, string> = {
  remind: 'Remind me', mark_stuck: 'Mark as stuck', expire: 'Let it expire',
  hand_off: 'Hand it to another agent', choose_for_me: 'Choose the recommended option for me',
};
export function actionLabel(type: string): string { return ACTION_LABELS[type] ?? type; }

/** "1 h", "30 min", "1 d 2 h": the same wording as the server (attention_rules.human). */
export function human(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) {
    const total = Math.round(s / 60);
    const h = Math.floor(total / 60), m = total % 60;
    return `${h} h${m ? ` ${m} min` : ''}`;
  }
  const d = Math.floor(s / 86400), h = Math.round((s % 86400) / 3600);
  return `${d} d${h ? ` ${h} h` : ''}`;
}

const KIND_WORD: Record<ScopeKind, string> = {
  decision: 'decision', questionnaire: 'questionnaire', permission: 'permission request', human_task: 'task for you',
};
const RISK_WORD: Record<ScopeRisk, string> = { low: 'low-risk', medium: 'medium-risk', high: 'high-risk' };

function article(word: string): string { return /^[aeiou]/i.test(word) ? 'an' : 'a'; }

function actionPhrase(a: RuleAction): string {
  switch (a.type) {
    case 'remind': return a.repeat_every_s ? `remind me every ${human(a.repeat_every_s)}` : 'remind me';
    case 'mark_stuck': return 'mark it as stuck';
    case 'expire': return a.mode === 'no_answer' ? 'close it with no answer' : "take the agent's default answer";
    case 'hand_off': return !a.to || a.to === 'supervisor' ? "hand it to the agent's supervisor" : `hand it to ${a.to}`;
    case 'choose_for_me': return a.by === 'supervisor'
      ? 'have its supervisor choose the recommended option for me' : 'choose the recommended option for me';
    default: return actionLabel(String(a.type)).toLowerCase();
  }
}

/** The rule as one sentence. */
export function ruleSentence(r: TimeoutRule): string {
  const when = r.trigger === 'no_progress'
    ? `When ${r.scope_agent ?? 'an agent'} makes no progress for ${human(r.after_s)}`
    : (() => {
      const noun = r.scope_kind ? KIND_WORD[r.scope_kind] : 'question';
      const adj = r.scope_risk ? `${RISK_WORD[r.scope_risk]} ` : '';
      const from = r.scope_agent ? ` from ${r.scope_agent}` : '';
      return `When ${article(adj || noun)} ${adj}${noun}${from} waits ${human(r.after_s)}`;
    })();
  const acts = (r.actions ?? []).map(actionPhrase);
  return acts.length ? `${when}: ${acts.join(', then ')}` : when;
}

export function ruleToForm(r: TimeoutRule): RuleForm {
  return {
    trigger: r.trigger, minutes: r.after_s / 60, agent: r.scope_agent ?? '',
    kind: r.scope_kind ?? '', risk: r.scope_risk ?? '', actions: (r.actions ?? []).map((a) => ({ ...a })),
    enabled: !!r.enabled,
  };
}

export const EMPTY_FORM: RuleForm = {
  trigger: 'unanswered', minutes: 60, agent: '', kind: '', risk: '', actions: [{ type: 'remind' }], enabled: true,
};

export type FormResult = TimeoutRule | { error: string };
export function isFormError(r: FormResult): r is { error: string } { return 'error' in r; }

/** The form as the rule to save, or the first thing wrong with it (in the page's words). */
export function formToRule(f: RuleForm, opts: { min_after_s?: number } = {}): FormResult {
  const min = opts.min_after_s ?? (f.trigger === 'no_progress' ? 600 : 60);
  // Minutes may carry seconds (a rule saved as 90 s reads as 1.5 min and saves back as 90 s).
  if (!Number.isFinite(f.minutes) || f.minutes <= 0) return { error: 'A number of minutes, please' };
  const afterS = Math.round(f.minutes * 60);
  if (afterS < min) return { error: `${human(min)} or more` };
  if (f.minutes > MAX_MINUTES) return { error: '7 days or less' };
  if (!f.actions.length) return { error: 'Pick at least one thing to do' };
  if (f.trigger === 'no_progress') {
    if (f.kind || f.risk) return { error: 'A no-progress rule picks an agent only' };
    const bad = f.actions.find((a) => a.type !== 'remind' && a.type !== 'mark_stuck');
    if (bad) return { error: `${actionLabel(String(bad.type))} needs a question to act on` };
  }
  const actsForYou = f.actions.some((a) => ACTS_FOR_YOU.includes(String(a.type)));
  if (actsForYou && (f.kind === 'permission' || f.risk === 'high')) return { error: ACTS_FOR_YOU_NOTE };
  for (const a of f.actions) {
    if (a.type === 'remind' && a.repeat_every_s !== undefined && a.repeat_every_s < MIN_REPEAT_MIN * 60) {
      return { error: `Repeat every ${MIN_REPEAT_MIN} min or more` };
    }
  }
  return {
    trigger: f.trigger, after_s: afterS, scope_agent: f.agent.trim() || null,
    scope_kind: f.kind || null, scope_risk: f.risk || null,
    actions: f.actions.map((a) => ({ ...a })), enabled: f.enabled ? 1 : 0,
  };
}

export interface ActionChoice { type: string; label: string; disabled: boolean; why: string | null; note: string | null }

/** The actions the add/edit form offers: each with its label; one the server says is not available is disabled with
 *  its reason. */
export function actionChoices(available: readonly AvailableAction[]): ActionChoice[] {
  return available.map((a) => ({
    type: a.type, label: actionLabel(a.type), disabled: !a.available, why: a.available ? null : (a.why ?? 'Not available'),
    note: ACTS_FOR_YOU.includes(a.type) ? ACTS_FOR_YOU_NOTE : null,
  }));
}

/** The body PUT/POST /api/attention/rules takes (enabled as a boolean, as the server asks). */
export function ruleBody(r: TimeoutRule): Record<string, unknown> {
  return {
    trigger: r.trigger, after_s: r.after_s, scope_agent: r.scope_agent, scope_kind: r.scope_kind,
    scope_risk: r.scope_risk, actions: r.actions, enabled: !!r.enabled, ...(r.name ? { name: r.name } : {}),
  };
}

/** A new action of `type` with its defaults (hand off: the supervisor; expire: the default; choose: the agent). */
export function newAction(type: string): RuleAction {
  if (type === 'hand_off') return { type, to: 'supervisor' };
  if (type === 'expire') return { type, mode: 'default' };
  if (type === 'choose_for_me') return { type, by: 'self' };
  return { type };
}

/** The repeat choices (minutes) for a remind action, always including the rule's own value. */
export function repeatChoices(current: number | undefined, base: readonly number[] = [0, 15, 30, 60, 120, 240]): number[] {
  const m = current ? current / 60 : 0;
  return [...new Set([...base, m])].sort((a, b) => a - b);
}
