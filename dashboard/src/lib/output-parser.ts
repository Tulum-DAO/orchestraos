/**
 * Output Parser — scans agent tmux output to detect interactive prompts.
 * Returns structured data for the ActionBar to render context-aware buttons.
 *
 * IMPORTANT: Claude Code has specific output formats that differ from generic CLI.
 * The patterns below are calibrated to match Claude Code's actual output.
 */

export interface ParsedPrompt {
  type: 'numbered_options' | 'permission' | 'yes_no' | 'expandable' | 'waiting' | 'auth_url' | 'none';
  options?: { num: string; text: string }[];
  permissionActions?: string[];
  expandLabel?: string;
  authUrl?: string;
}

// Claude Code permission prompts:
//   "? Allow Read(file.ts)"
//   "? Allow Bash(npm install)"
//   "? Allow mcp__tool(args)"
//   Also: "(Y)es (N)o (A)lways (D)eny"
const PERMISSION_PATTERNS = [
  /^\?\s+Allow\s+\w+/,                       // Claude Code: "? Allow Tool(args)"
  /\(Y\)es.*\(N\)o/i,                        // Claude Code permission choices
  /\bAlways allow\b/i,                        // Claude Code always-allow option
  /allow this action/i,
  /Do you want to/i,
];

const YES_NO_PATTERNS = [
  /\(Y\/n\)/i,
  /\(y\/N\)/i,
  /\[Y\/n\]/i,
  /\[y\/N\]/i,
  /yes\/no/i,
  /\(yes\).*\(no\)/i,
  /^\?\s+.*\(Y\).*\(N\)/,                    // Claude Code: "? Question (Y)es (N)o"
];

const EXPAND_PATTERNS = [
  /ctrl\+o\s+to\s+expand/i,
  /\(ctrl\+o\s+to\s+expand\)/i,
  /Read\s+\d+\s+files?\s+\(ctrl\+o/i,
  /\d+\s+tool\s+uses?\s+\(ctrl\+o/i,
  /\+\d+\s+lines?\s+\(ctrl\+o/i,
];

// Numbered option patterns — one per line
const NUMBERED_OPTION = /^(?:(\d+)\.\s+|\((\d+)\)\s+|(\d+)\)\s+)(.+)/;

// Claude Code inline rating: "1: Bad    2: Fine   3: Good   0: Dismiss"
// Simple pattern: digit + colon/dot + space + word(s)
const INLINE_OPTION_SIMPLE = /(\d+)[.:]\s+([A-Za-z][\w\s-]*?)(?=\s{2,}\d+[.:]|\s*$)/g;

const WAITING_INPUT = /^[❯›>$]\s*$/;

const AUTH_URL_PATTERN = /https:\/\/[^\s]*claude\.ai[^\s]*/;
const AUTH_PROMPT_PATTERN = /login|authenticate|expired|sign in|authorization/i;

export function parseAgentOutput(lines: string[]): ParsedPrompt {
  // Scan last 20 lines for prompts (increased from 15 for more context)
  const recent = lines.slice(-20);
  const joined = recent.join('\n');

  // Check for auth URL (highest priority — user needs to act on this)
  if (AUTH_PROMPT_PATTERN.test(joined)) {
    for (const line of recent) {
      const urlMatch = line.match(AUTH_URL_PATTERN);
      if (urlMatch) {
        return { type: 'auth_url', authUrl: urlMatch[0] };
      }
    }
  }

  // Check for expandable content (ctrl+o)
  for (const line of recent) {
    for (const pattern of EXPAND_PATTERNS) {
      const match = line.match(pattern);
      if (match) {
        return {
          type: 'expandable',
          expandLabel: line.trim().replace(/[()]/g, '').trim(),
        };
      }
    }
  }

  // Check for permission prompts (Allow/Deny) — Claude Code format
  for (const line of recent) {
    for (const pattern of PERMISSION_PATTERNS) {
      if (pattern.test(line.trim())) {
        const actions: string[] = [];
        // Scan nearby lines for available actions
        const context = recent.slice(-10).join('\n');
        if (/\(Y\)es|\bAllow\b|yes/i.test(context)) actions.push('Allow');
        if (/\(N\)o|\bDeny\b|\bReject\b|no/i.test(context)) actions.push('Deny');
        if (/\(A\)lways|\bAlways allow\b/i.test(context)) actions.push('Always allow');
        if (actions.length === 0) {
          // Default for "? Allow ..." prompts
          actions.push('Allow', 'Deny');
        }
        return { type: 'permission', permissionActions: actions };
      }
    }
  }

  // Check for yes/no prompts
  for (const line of recent) {
    for (const pattern of YES_NO_PATTERNS) {
      if (pattern.test(line.trim())) {
        return { type: 'yes_no' };
      }
    }
  }

  // Check for numbered options — try multi-line first, then inline
  const opts: { num: string; text: string }[] = [];
  const seen = new Set<string>();
  let lastOptIdx = -1;

  // Multi-line options (1. Item\n2. Item\n...)
  for (let idx = 0; idx < recent.length; idx++) {
    const match = recent[idx].trim().match(NUMBERED_OPTION);
    if (match) {
      const num = match[1] || match[2] || match[3];
      const text = match[4].trim();
      if (!seen.has(num)) { seen.add(num); opts.push({ num, text }); }
      lastOptIdx = idx;
    }
  }

  if (opts.length >= 2) {
    const afterOpts = recent.slice(lastOptIdx + 1);
    const hasTextAfter = afterOpts.some(l => {
      const t = l.trim();
      return t && !WAITING_INPUT.test(t);
    });
    if (!hasTextAfter) {
      return { type: 'numbered_options', options: opts };
    }
  }

  // Inline options: "1: Bad    2: Fine   3: Good   0: Dismiss"
  // Claude Code uses this format for ratings and compact choices
  for (let idx = recent.length - 1; idx >= Math.max(0, recent.length - 5); idx--) {
    const line = recent[idx].trim();
    const inlineMatches: { num: string; text: string }[] = [];
    const inlineSeen = new Set<string>();
    let m;
    const rx = new RegExp(INLINE_OPTION_SIMPLE.source, 'g');
    while ((m = rx.exec(line)) !== null) {
      const num = m[1];
      const text = m[2].trim();
      if (!inlineSeen.has(num) && text.length > 0) {
        inlineSeen.add(num);
        inlineMatches.push({ num, text });
      }
    }
    if (inlineMatches.length >= 2) {
      // Verify nothing substantial comes after this line
      const afterLine = recent.slice(idx + 1);
      const hasTextAfter = afterLine.some(l => {
        const t = l.trim();
        return t && !WAITING_INPUT.test(t);
      });
      if (!hasTextAfter) {
        return { type: 'numbered_options', options: inlineMatches };
      }
    }
  }

  // Check for waiting input prompt
  for (const line of recent.slice(-3)) {
    if (WAITING_INPUT.test(line.trim())) {
      return { type: 'waiting' };
    }
  }

  return { type: 'none' };
}
