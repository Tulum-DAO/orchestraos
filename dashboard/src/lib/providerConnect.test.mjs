/**
 * RED-first for the disconnected-provider modal (operator, 2026-09-18: clicking a
 * disconnected provider opens a modal with a TMUX / OAUTH toggle at the top).
 *
 * The MODE LOGIC is pure and therefore testable: which of the two ways to connect are
 * actually available for a given provider, which one opens by default, and what each one
 * dispatches. The rendering is not tested here (no component harness) — see the PR body.
 *
 * The rule these tests encode: a mode that cannot do what it says is never OFFERED as
 * usable. Same discipline as the "Add a provider" tile — no control that pretends.
 *
 *   node --experimental-strip-types dashboard/src/lib/providerConnect.test.mjs
 */
import assert from 'node:assert';
import { connectModes, defaultMode, connectPlan, reasonText } from './providerConnect.ts';

const notInstalled = { id: 'gemini', label: 'Gemini', installed: false, authed: false, auth_reason: 'not-installed' };
const installedLoggedOut = { id: 'claude', label: 'Claude', installed: true, authed: false, auth_reason: 'loggedIn=false' };
const unverified = { id: 'codex', label: 'Codex', installed: true, authed: 'unverified', auth_reason: 'auth-probe-cmd-failed' };
const connected = { id: 'claude', label: 'Claude', installed: true, authed: true };

// --- which modes are available -------------------------------------------------------------
{
  // CLI absent: neither way can work yet. Both modes are listed (the toggle still renders,
  // so the operator sees what the two options ARE) but both are unavailable, with a reason.
  const m = connectModes(notInstalled);
  assert.deepEqual(m.map((x) => x.id), ['tmux', 'oauth']);
  assert.equal(m.every((x) => x.available === false), true);
  assert.match(m[0].unavailable_reason, /not installed/i);
  assert.match(m[1].unavailable_reason, /not installed/i);
  // and the two reasons DIFFER: identical copy on both tabs makes the toggle look broken
  assert.notEqual(m[0].unavailable_reason, m[1].unavailable_reason);
  assert.match(m[0].unavailable_reason, /terminal/i);
  assert.match(m[1].unavailable_reason, /browser/i);
}
{
  // CLI present but logged out: BOTH ways genuinely work — the terminal walks the login,
  // and the browser sign-in is what that login opens.
  const m = connectModes(installedLoggedOut);
  assert.equal(m.find((x) => x.id === 'tmux').available, true);
  assert.equal(m.find((x) => x.id === 'oauth').available, true);
}
{
  // W1: 'unverified' is NEVER treated as usable, but it is also not a refusal — the
  // operator can still try to connect, because the probe failing to RUN is not proof of
  // being logged out.
  const m = connectModes(unverified);
  assert.equal(m.find((x) => x.id === 'tmux').available, true);
}

// --- which mode opens first ----------------------------------------------------------------
{
  // The terminal is the one that always works when the CLI is there, so it leads.
  assert.equal(defaultMode(installedLoggedOut), 'tmux');
  // Nothing is available, so the toggle still opens on the first mode rather than on none.
  assert.equal(defaultMode(notInstalled), 'tmux');
}

// --- what each mode actually dispatches ------------------------------------------------------
{
  const plan = connectPlan(installedLoggedOut, 'tmux');
  assert.equal(plan.kind, 'login-shell');
  assert.equal(plan.provider, 'claude');           // the provider CLICKED, not "the first installed"
  assert.equal(plan.endpoint, '/api/agents/login-shell');
}
{
  const plan = connectPlan({ ...installedLoggedOut, id: 'codex', label: 'Codex' }, 'oauth');
  assert.equal(plan.kind, 'browser-signin');
  assert.equal(plan.provider, 'codex');
  // codex signs in with its own subcommand; claude/gemini sign in from inside the CLI
  assert.match(plan.command, /codex login/);
}
{
  const plan = connectPlan(installedLoggedOut, 'oauth');
  assert.match(plan.command, /claude/);
  assert.match(plan.detail, /browser/i);
}
{
  // A mode that is not available yields a plan that DOES NOTHING but says why.
  const plan = connectPlan(notInstalled, 'tmux');
  assert.equal(plan.kind, 'blocked');
  assert.match(plan.detail, /install/i);
}

// --- the reason line: this is the iOS fix ----------------------------------------------------
{
  // On a phone the tile's title attribute is not a real affordance, so the modal is where
  // the reason has to appear. It must never be empty for a disconnected provider.
  assert.match(reasonText(notInstalled), /not-installed/);
  assert.match(reasonText(installedLoggedOut), /loggedIn=false/);
  assert.equal(reasonText(connected), '');
  // a provider with no reason string still gets an honest sentence, never a blank
  assert.notEqual(reasonText({ id: 'x', label: 'X', installed: true, authed: false }), '');
}

console.log('providerConnect.test.mjs: all assertions passed');
