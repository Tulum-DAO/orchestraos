/**
 * providerConnect.ts — the two ways to connect a DISCONNECTED provider.
 *
 * Operator, 2026-09-18: clicking a disconnected provider should open a modal with a toggle
 * at the top between TMUX and OAUTH. Those are the two honest routes on this install:
 *
 *   TMUX  — open a terminal here and walk the CLI's own login. Always works when the CLI is
 *           present, because it is the CLI doing the work and the operator can see it.
 *   OAUTH — sign in with the provider account in a browser. On every provider in the
 *           catalogue that browser flow is STARTED BY THE CLI (claude's /login, codex login,
 *           gemini's auth), which is why this mode names the command that opens it rather
 *           than pretending the dashboard can mint an OAuth URL of its own.
 *
 * The rule, same as the "Add a provider" tile: a mode that cannot do what it says is never
 * offered as usable. Both modes are always LISTED so the operator can see what the choices
 * are, but an unavailable one carries the reason instead of a button.
 *
 * This module is also where the iOS gap gets closed. A red tile's reason used to reach the
 * operator through the tile's `title` attribute, which iOS Safari does not reliably surface
 * on long-press — so on a phone the reason was effectively unreachable outside the
 * "Add a provider" panel. reasonText() puts it in the modal, on the surface, in words.
 */
export interface ProviderLike {
  id: string;
  label: string;
  installed: boolean;
  authed: boolean | 'unverified';
  auth_reason?: string | null;
}

export type ModeId = 'tmux' | 'oauth';

export interface ConnectMode {
  id: ModeId;
  label: string;
  blurb: string;
  available: boolean;
  unavailable_reason?: string;
}

export type ConnectPlan =
  | { kind: 'login-shell'; provider: string; endpoint: string; detail: string; install_command?: string }
  | { kind: 'browser-signin'; provider: string; command: string; detail: string }
  | { kind: 'blocked'; provider: string; detail: string };

/** The binary that actually has to be installed. Gemini's CLI is `agy`, not `gemini`. */
const CLI_FOR_ID: Record<string, string> = { claude: 'claude', gemini: 'agy', codex: 'codex' };
export const cliFor = (id: string): string => CLI_FOR_ID[id] || id;

/** How each provider's browser sign-in is started. codex has its own subcommand; the
 *  others sign in from inside the running CLI. */
export function signinCommand(id: string): string {
  const cli = cliFor(id);
  if (id === 'codex') return 'codex login';
  if (id === 'claude') return 'claude   (then type /login)';
  return `${cli}   (then follow its sign-in prompt)`;
}

/** How you actually get this CLI onto a blank machine. Shown IN the terminal tab so the
 *  person can paste it where it runs, rather than being told to go elsewhere. */
export function installCommand(id: string): string {
  if (id === 'claude') return 'npm i -g @anthropic-ai/claude-code';
  if (id === 'codex') return 'npm i -g @openai/codex';
  if (id === 'gemini') return 'npm i -g @google/antigravity-cli';
  return `# install the ${id} CLI, then run it once to sign in`;
}

export function installHint(id: string): string {
  const cli = cliFor(id);
  return `\`${cli}\` is not installed on this machine.`;
}

export function connectModes(p: ProviderLike): ConnectMode[] {
  const cli = cliFor(p.id);
  const missing = !p.installed;
  // Mode-specific even when BLOCKED: if both tabs said the same sentence the toggle would
  // look broken, and the operator would have no way to tell what he is choosing between.
  const oauthBlocked = `${installHint(p.id)} The browser sign-in is started BY that CLI, so it has to exist first — use the TMUX tab to install it.`;
  return [
    {
      id: 'tmux',
      label: 'TMUX',
      blurb: missing
        ? `Open a terminal here and install \`${cli}\`, then run it to sign in — all without leaving this window.`
        : `Open a terminal here and run \`${cli}\` — you sign in inside it and watch it happen.`,
      // ALWAYS available: on a blank machine the terminal is where you INSTALL the CLI,
      // so refusing to open one because the CLI is absent is exactly backwards — it made
      // the tile a dead end for the only person who needed it most.
      available: true,
      unavailable_reason: undefined,
    },
    {
      id: 'oauth',
      label: 'OAUTH',
      blurb: 'Sign in with your provider account in a browser. The CLI opens that page for you.',
      available: !missing,
      unavailable_reason: missing ? oauthBlocked : undefined,
    },
  ];
}

/** The terminal leads: when the CLI is present it always works, and when it is not,
 *  the toggle still has to open on something rather than on nothing. */
export function defaultMode(_p: ProviderLike): ModeId {
  return 'tmux';
}

export function connectPlan(p: ProviderLike, mode: ModeId): ConnectPlan {
  const modes = connectModes(p);
  const chosen = modes.find((m) => m.id === mode);
  if (!chosen || !chosen.available) {
    return { kind: 'blocked', provider: p.id, detail: chosen?.unavailable_reason || installHint(p.id) };
  }
  if (mode === 'tmux') {
    return {
      kind: 'login-shell',
      provider: p.id,
      endpoint: '/api/agents/login-shell',
      detail: p.installed
        ? `Opens a terminal running \`${cliFor(p.id)}\` so you can sign in here.`
        : `Opens a terminal on this machine. Install \`${cliFor(p.id)}\` in it, then run it to sign in.`,
      install_command: p.installed ? undefined : installCommand(p.id),
    };
  }
  return {
    kind: 'browser-signin',
    provider: p.id,
    command: signinCommand(p.id),
    detail: 'Run this, and the CLI opens your browser to sign in. Come back when it confirms.',
  };
}

/** Why this provider is disconnected, in words, for the modal. Never blank for a provider
 *  that cannot be used — a blank reason is the failure this exists to prevent. */
export function reasonText(p: ProviderLike): string {
  const usable = p.installed && p.authed === true;
  if (usable) return '';
  if (p.auth_reason) return p.auth_reason;
  if (!p.installed) return 'not-installed';
  if (p.authed === 'unverified') return 'could not verify — the sign-in check did not run';
  return 'signed out';
}
