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
  if (id === 'codex') return 'codex login --device-auth';
  if (id === 'claude') return 'claude   (then type /login)';
  return `${cli}   (then follow its sign-in prompt)`;
}

/** How you actually get this CLI onto a blank machine. Shown IN the terminal tab so the
 *  person can paste it where it runs, rather than being told to go elsewhere. */
/** The npm package, ONLY where one genuinely exists. Verified against the registry:
 *  @openai/codex and @anthropic-ai/claude-code resolve; there is NO npm package for agy —
 *  `@google/antigravity-cli` 404s. It was a name I invented, the operator ran it, and it
 *  failed in his hands. A command that cannot work is worse than no command: it spends the
 *  newcomer's trust before anything else gets a chance to. */
export function npmPackage(id: string): string | null {
  if (id === 'claude') return '@anthropic-ai/claude-code';
  if (id === 'codex') return '@openai/codex';
  return null;
}

/** A shell command for a CLI that is NOT an npm package. `agy` ships as a standalone
 *  binary with its own installer — verified by RUNNING it in a clean container: it exits 0,
 *  writes ~/.local/bin/agy, appends that dir to the shell profile's PATH, and the runtime
 *  probe then reports gemini installed=true (auth-file-missing, i.e. installed but not yet
 *  signed in). Source: Google's own CLI install docs, corroborated by the binary on a
 *  machine that already had it living at exactly that path. */
export function installShellCommand(id: string): string | null {
  if (npmPackage(id)) return null;
  if (id === 'gemini') return `curl -fsSL https://antigravity.google/cli/install.sh | bash${RELOAD_SUFFIX}`;
  return null;
}

/** For a CLI with neither an npm package nor a known installer, say what is TRUE rather
 *  than printing a command that cannot work. */
/** What a person actually has to know to get this provider connected, in order.
 *
 *  Every line here was established by RUNNING it tonight, not inferred: the installer and
 *  its destination, what it does when the binary already exists, how sign-in behaves with
 *  no browser (a server almost never has one), and which file the runtime probe watches to
 *  decide "authed". The modal is where a stranger is standing when they need this, so it
 *  goes here rather than in docs they will not open. */
export function connectSteps(id: string): string[] {
  if (id === 'gemini') {
    return [
      'The installer drops the `agy` binary in ~/.local/bin and adds that folder to your shell profile. The `exec bash -l` on the end reloads the shell so the new binary is found straight away — without it, this terminal keeps the PATH it started with and `agy` reads as "command not found" even though it installed fine.',
      'Already installed? The installer will say so and stop. It self-updates in the background, so you do not need to reinstall. For a genuinely fresh copy, `rm ~/.local/bin/agy` first.',
      'Then run `agy` here to sign in. On a server there is no browser to open, so it prints a URL — open it on your own machine, sign in, and paste the code it gives you back into this terminal.',
      'Signing in writes ~/.gemini/antigravity-cli/antigravity-oauth-token. That file is exactly what this sheet checks, so once it exists, reopen the sheet and Gemini turns from red to selectable.',
    ];
  }
  if (id === 'codex') {
    return [
      'The install goes to ~/.local/bin, which is already on PATH — no sudo, and no root needed. `npm i -g` on its own fails here with EACCES because npm\'s global folder belongs to root and this terminal is not root.',
      'Then sign in with `codex login --device-auth`. Use that flag, not a bare `codex login`: the plain form starts a callback server on port 1455 of THIS machine and waits for your browser to hit it — which cannot happen when your browser is somewhere else. Device auth prints a short code instead and the CLI and browser talk through OpenAI, so no port has to be reachable.',
      'Open the URL it prints, enter the code, and approve. The code is good for about 15 minutes.',
      'Signing in writes ~/.codex/auth.json, which is exactly what this sheet checks. Reopen the sheet, or hit "check again", once it exists.',
    ];
  }
  if (id === 'claude') {
    return [
      'The install goes to ~/.local/bin, which is already on PATH — no sudo needed.',
      'Then run `claude` here and type /login to sign in.',
      'Reopen the sheet once it confirms you are signed in; it re-probes every time it opens.',
    ];
  }
  return [];
}

export function installNote(id: string): string | null {
  if (npmPackage(id) || installShellCommand(id)) return null;
  return `Install the ${id} CLI however its vendor distributes it, put it on your PATH, then run it here to sign in.`;
}

/** Every install command ends by REPLACING the shell with a login shell.
 *
 *  The operator hit this: he installed agy successfully, then `agy` said "command not
 *  found" in the very pane that had just installed it. Nothing was broken — the tmux
 *  session is long-lived and REUSED, so it was created BEFORE the install and its PATH
 *  predates it. The installer appends ~/.local/bin to the shell profile, which only a NEW
 *  shell reads. [M]: with a stale PATH `command -v agy` finds nothing; after `exec bash -l`
 *  the same session resolves /home/<user>/.local/bin/agy and reports 1.2.7.
 *
 *  `hash -r` is not enough — that clears the command cache, not PATH. `exec bash -l`
 *  replaces the shell in place, so the tmux session and its scrollback survive. */
const RELOAD_SUFFIX = ' && exec bash -l';

export function installCommand(id: string): string | null {
  // --prefix "$HOME/.local", NOT a bare `npm i -g`. The operator ran the bare form and got
  // EACCES on /usr/lib/node_modules: npm's global prefix is /usr (root-owned) and the
  // terminal runs as the unprivileged service user. A command that needs root is a command
  // that does not work, and telling a stranger to sudo-install a CLI is worse advice still.
  // ~/.local/bin is already on PATH, so the probe that decides "installed" sees it there.
  const pkg = npmPackage(id);
  if (!pkg) return installShellCommand(id);   // never an invented command: see installNote()
  return `npm i -g --prefix "$HOME/.local" ${pkg}${RELOAD_SUFFIX}`;
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
      install_command: p.installed ? undefined : (installCommand(p.id) ?? undefined),
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
