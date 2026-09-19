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
import { connectModes, defaultMode, connectPlan, reasonText, installCommand, installNote, connectSteps, signinCommand } from './providerConnect.ts';

const notInstalled = { id: 'gemini', label: 'Gemini', installed: false, authed: false, auth_reason: 'not-installed' };
const installedLoggedOut = { id: 'claude', label: 'Claude', installed: true, authed: false, auth_reason: 'loggedIn=false' };
const unverified = { id: 'codex', label: 'Codex', installed: true, authed: 'unverified', auth_reason: 'auth-probe-cmd-failed' };
const connected = { id: 'claude', label: 'Claude', installed: true, authed: true };

// --- which modes are available -------------------------------------------------------------
{
  // CLI absent. The operator found the dead end this replaces: with TMUX marked
  // unavailable, a blank machine could not progress from either tab — and the terminal is
  // precisely where you INSTALL the CLI. So TMUX is ALWAYS available; refusing to open a
  // shell because the CLI is missing was exactly backwards.
  const m = connectModes(notInstalled);
  assert.deepEqual(m.map((x) => x.id), ['tmux', 'oauth']);
  assert.equal(m.find((x) => x.id === 'tmux').available, true);
  assert.equal(m.find((x) => x.id === 'oauth').available, false);
  assert.match(m.find((x) => x.id === 'oauth').unavailable_reason, /not installed/i);
  // and OAUTH points at the tab that can fix it, rather than dead-ending
  assert.match(m.find((x) => x.id === 'oauth').unavailable_reason, /tmux/i);
  // the terminal tab says it is for INSTALLING, not just signing in
  assert.match(m.find((x) => x.id === 'tmux').blurb, /install/i);
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
  // A missing CLI yields a REAL terminal plan, because a blank machine's whole path runs
  // through that shell. It carries an install command ONLY where a real package exists.
  const codexMissing = { id: 'codex', label: 'Codex', installed: false, authed: false, auth_reason: 'not-installed' };
  const plan = connectPlan(codexMissing, 'tmux');
  assert.equal(plan.kind, 'login-shell');
  assert.match(plan.detail, /install/i);
  assert.match(plan.install_command, /npm i -g/);

  // gemini has no npm package but DOES have a real installer, so the plan carries that —
  // never the fabricated npm name, and never nothing when something true exists.
  const gem = connectPlan(notInstalled, 'tmux');
  assert.equal(gem.kind, 'login-shell');
  assert.match(gem.install_command, /antigravity\.google\/cli\/install\.sh/);
  assert.doesNotMatch(gem.install_command, /npm/);
}
{
  // OAUTH on a missing CLI is still blocked — the browser flow is started BY the CLI.
  const plan = connectPlan(notInstalled, 'oauth');
  assert.equal(plan.kind, 'blocked');
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

// --- the install command is real, per provider -------------------------------------------
assert.match(installCommand('codex'), /@openai\/codex/);
// gemini has NO npm package — @google/antigravity-cli 404s on the registry. It must return
// null and a truthful note, never a fabricated command (the operator ran the invented one).
// gemini is NOT an npm package (@google/antigravity-cli 404s — I invented that name and the
// operator ran it). It has a REAL installer, verified by running it in a clean container:
// exit 0, binary at ~/.local/bin/agy, and the probe then reported gemini installed=true.
assert.match(installCommand('gemini'), /antigravity\.google\/cli\/install\.sh/);
assert.doesNotMatch(installCommand('gemini'), /npm/);
assert.equal(installNote('gemini'), null);     // it has a real command, so no fallback note
assert.equal(installNote('codex'), null);
assert.match(installCommand('claude'), /@anthropic-ai\/claude-code/);
// The bare `npm i -g` form FAILED for the operator with EACCES: npm's prefix is /usr and the
// terminal runs unprivileged. Every install command must target a user-writable prefix.
for (const id of ['codex', 'claude']) {
  assert.match(installCommand(id), /--prefix "\$HOME\/\.local"/,
    `${id} install command must not need root`);
  assert.doesNotMatch(installCommand(id), /^sudo /, `${id} must not tell a stranger to sudo`);
}

// --- every install command must leave the shell ABLE TO RUN what it just installed -------
// The operator installed agy and then got "agy: command not found" in the same pane: the
// tmux session is reused, so it predated the install and its PATH was stale. A command that
// installs a binary the next line cannot run is not a working instruction.
for (const id of ['codex', 'claude', 'gemini']) {
  assert.match(installCommand(id), /exec bash -l$/,
    `${id}: install command must reload the shell so PATH picks up the new binary`);
}

// --- the steps carry what was LEARNED BY RUNNING IT, not generic advice ------------------
{
  const gem = connectSteps('gemini').join(' ');
  assert.match(gem, /~\/\.local\/bin/);                  // where the binary lands
  assert.match(gem, /command not found/);                  // the stale-PATH trap he hit
  assert.match(gem, /self-update/);                        // already-installed case
  assert.match(gem, /no browser/);                         // server sign-in reality
  assert.match(gem, /antigravity-oauth-token/);            // the file the probe watches
  const cod = connectSteps('codex').join(' ');
  assert.match(cod, /EACCES/);                             // why a bare npm -g fails here
  assert.match(cod, /auth\.json/);
  // A bare `codex login` opens a callback server on port 1455 of the SERVER and waits for a
  // browser that is somewhere else. `--device-auth` is a real flag, measured by effect from
  // `codex login --help` on codex-cli 0.153.4 (the version this install actually carries).
  // The steps must name it, and must say why.
  assert.match(cod, /--device-auth/);
  assert.match(cod, /1455/);
  assert.match(signinCommand('codex'), /--device-auth/);
  assert.equal(connectSteps('nope').length, 0);            // no invented steps for unknowns
}

console.log('providerConnect.test.mjs: all assertions passed');
