/**
 * Remove secrets from text that leaves the server (the Markdown export). SHAPE ONLY: every rule
 * matches a known secret format or a name that says "secret"; nothing is guessed from how random
 * a string looks, so hashes, UUIDs and commit SHAs stay readable.
 *
 * Each match becomes `[redacted: <kind>]`; the result counts them so the export can say so.
 */
export interface Redaction {
  text: string;
  count: number;
  kinds: Record<string, number>;
}

type Rule = { kind: string; re: RegExp; keep?: (m: RegExpExecArray) => string };

const NAME = String.raw`[A-Za-z0-9_.-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY)[A-Za-z0-9_.-]*`;

const RULES: Rule[] = [
  // A PEM private key, whole block.
  { kind: 'private key', re: /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g },
  // Vendor key formats.
  { kind: 'Anthropic key', re: /\bsk-ant-[A-Za-z0-9_-]{20,}/g },
  { kind: 'OpenAI key', re: /\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}/g },
  { kind: 'Stripe key', re: /\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}/g },
  { kind: 'GitHub token', re: /\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})/g },
  { kind: 'Slack token', re: /\bxox[abprs]-[A-Za-z0-9-]{10,}/g },
  { kind: 'AWS key', re: /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g },
  { kind: 'Google key', re: /\bAIza[A-Za-z0-9_-]{35}\b/g },
  { kind: 'Google OAuth secret', re: /\bGOCSPX-[A-Za-z0-9_-]{20,}/g },
  { kind: 'Google OAuth token', re: /\bya29\.[A-Za-z0-9_-]{20,}/g },
  { kind: 'JWT', re: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g },
  // "Authorization: Bearer <token>" and the like: keep the word, drop the token.
  { kind: 'bearer token', re: /\b(Bearer)\s+[A-Za-z0-9._~+/=-]{16,}/gi, keep: (m) => `${m[1]} ` },
  // A URL with a password in it: keep scheme, user and host.
  { kind: 'URL password', re: /\b([a-z][a-z0-9+.-]*:\/\/[^\s:/@]+):[^\s@/]+@/gi, keep: (m) => `${m[1]}:` },
  // ?token=... / &key=... style query values: keep the name.
  { kind: 'URL token', re: /([?&](?:access_?token|token|api_?key|key|secret|password|sig|signature)=)[^\s&#"']+/gi, keep: (m) => m[1] },
  // NAME=value / NAME: value / "NAME": "value" where the NAME says it is a secret: keep the name.
  { kind: 'named secret', re: new RegExp(String.raw`(["']?\b${NAME}["']?\s*[:=]\s*["']?)([^\s"',;}{]{4,})`, 'gi'), keep: (m) => m[1] },
];

export function redactSecrets(input: string, exact: string[] = []): Redaction {
  let text = String(input ?? '');
  const kinds: Record<string, number> = {};
  const note = (k: string) => { kinds[k] = (kinds[k] || 0) + 1; };
  // The install's own secrets, by exact value (the gateway token, pairing codes), first.
  for (const v of exact) {
    if (typeof v !== 'string' || v.length < 8) continue;
    const parts = text.split(v);
    if (parts.length > 1) {
      for (let i = 1; i < parts.length; i++) note('server secret');
      text = parts.join('[redacted: server secret]');
    }
  }
  for (const r of RULES) {
    text = text.replace(r.re, (...args) => {
      const m = args.slice(0, -2) as unknown as RegExpExecArray;
      if (String(m[0]).includes('[redacted:')) return m[0];
      note(r.kind);
      return `${r.keep ? r.keep(m) : ''}[redacted: ${r.kind}]`;
    });
  }
  const count = Object.values(kinds).reduce((a, b) => a + b, 0);
  return { text, count, kinds };
}
