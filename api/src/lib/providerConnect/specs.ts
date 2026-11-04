/**
 * What each provider needs to be installed and signed in on a server with no browser and no root
 * (provider-install-research.md, provider-install-capture-20261104.md). Constants only: nothing a request sends ever
 * reaches these command lines.
 */

/** The Codex CLI version installed from npm. One constant: the version a test install runs and signs in with
 *  (2026-11-04). Unpinned npm drifts (one install's 0.153.4 → 0.166.0 in one install), so a bump is a deliberate edit. */
export const CODEX_VERSION = '0.166.0';

export type ConnectProviderId = 'codex' | 'grok' | 'gemini';

export interface ProviderSpec {
  id: ConnectProviderId;
  label: string;
  /** The binary (gemini's is agy). */
  cli: string;
  /** The tmux session the sign-in runs in. The same name the old terminal flow used, so a leftover is replaced. */
  session: string;
  /** Install command, run with `bash -lc` as the API's own user. */
  install: string;
  /** Preflight reachability check before an install: the host the installer downloads from. */
  installUrl: string;
  /** Tools the install and sign-in need on PATH. */
  tools: string[];
  /** The sign-in command (device / no-browser mode). */
  signin: string;
  /** device: the CLI polls until the code is approved. paste: the user pastes a code back into the CLI. */
  kind: 'device' | 'paste';
  /** Where else the installer may put the binary (grok: ~/.grok/bin, linked into ~/.local/bin). Relative to $HOME. */
  altBin?: string;
}

export const SPECS: Record<ConnectProviderId, ProviderSpec> = {
  codex: {
    id: 'codex', label: 'Codex', cli: 'codex', session: 'login-codex',
    install: `npm i -g --prefix "$HOME/.local" @openai/codex@${CODEX_VERSION}`,
    installUrl: 'https://registry.npmjs.org/',
    tools: ['tmux', 'npm'],
    signin: 'codex login --device-auth',
    kind: 'device',
  },
  grok: {
    id: 'grok', label: 'Grok', cli: 'grok', session: 'login-grok',
    install: 'curl -fsSL https://x.ai/cli/install.sh | bash',
    installUrl: 'https://x.ai/cli/install.sh',
    tools: ['tmux', 'curl'],
    signin: 'grok login --device-auth',
    kind: 'device',
    altBin: '.grok/bin/grok',
  },
  gemini: {
    id: 'gemini', label: 'Gemini', cli: 'agy', session: 'login-agy',
    install: 'curl -fsSL https://antigravity.google/cli/install.sh | bash',
    installUrl: 'https://antigravity.google/cli/install.sh',
    tools: ['tmux', 'curl'],
    // No `agy login`: sign-in is the TUI's first screen (Google OAuth, then paste the code back).
    signin: 'agy',
    kind: 'paste',
  },
};

export function specFor(id: unknown): ProviderSpec | null {
  return typeof id === 'string' && Object.prototype.hasOwnProperty.call(SPECS, id) ? SPECS[id as ConnectProviderId] : null;
}

/** The script the sign-in tmux session runs: our PATH first (the installers put binaries in ~/.local/bin and
 *  ~/.grok/bin), then the CLI, then an exit marker the parsers read, then a pause so the last screen stays readable.
 *  Built from constants only. */
export function signinScript(spec: ProviderSpec): string {
  return `export PATH="$HOME/.local/bin:$HOME/.grok/bin:$PATH"; ${spec.signin}; echo __OOS_EXIT=$?; sleep 1800`;
}

/** The minimum free space in $HOME before an install (agy is ~230 MB, grok ~200 MB). */
export const MIN_FREE_BYTES = 1024 * 1024 * 1024;
