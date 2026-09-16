/**
 * db-integrity.ts — PRAGMA integrity_check output classifier (DEC-1788702356376911 v2).
 *
 * The single job: turn integrity_check rows into a recovery class so db.ts never
 * again wipes tasks.db over a non-fatal finding. Pure + unit-tested; db.ts owns
 * the side effects (REINDEX / archive / halt) keyed off the class.
 *
 * Classification is fail-SAFE toward preserving data:
 *   OK     — the single row 'ok'.
 *   BENIGN — EVERY row matches the strict allowlist (only 'Page N is never used',
 *            SQLite's harmless freelist note). Serve the existing DB.
 *   INDEX  — no fatal rows AND at least one row is an index-scoped finding
 *            ('row N missing from index …', 'wrong # of entries in index …').
 *            REINDEX candidate (db.ts backs up first, per amendment A1/A2).
 *   FATAL  — anything else: a bare 'malformed', an unrecognized finding, empty
 *            output (check produced nothing → cannot confirm ok), or a thrown
 *            check (THROWN sentinel). Archive-consistently + HALT, never recreate.
 *
 * Whole-set semantics: the strongest class present wins (FATAL > INDEX > BENIGN),
 * which is why db.ts must read ALL rows (no `integrity_check(1)` cap) — a benign
 * note above a fatal finding must not mask it.
 */

export enum IntegrityClass {
  OK = 'OK',
  BENIGN = 'BENIGN',
  INDEX = 'INDEX',
  FATAL = 'FATAL',
}

export interface IntegrityResult {
  cls: IntegrityClass;
  rows: string[];
  /** rows that drove the classification (for the alarm payload) */
  findings: string[];
}

// Strict benign allowlist: ONLY SQLite's freelist "never used" note. The REAL
// engine (verified against SQLite 3.53 better-sqlite3 AND 3.45 python) emits the
// COLON form "Page N: never used"; older docs/builds use "Page N is never used".
// Accept both, anchored; everything else escalates.
const BENIGN_RE = /^Page \d+(: | is )never used$/;
// Index-scoped findings that REINDEX can repair with no data loss.
const INDEX_RE = /(missing from index|wrong # of entries in index)/i;
// integrity_check groups per-database findings under a "*** in database <name> ***"
// PREAMBLE line — it is a header, not a finding. The engine also returns a whole
// group as ONE multi-line row, so normalization must split on '\n' before matching
// (the false-green that failed the gate: the preamble+note arrived as a single row
// and never matched a per-line regex).
const PREAMBLE_RE = /^\*\*\* in database \S+ \*\*\*$/;

// Flatten integrity_check output to individual finding lines: split every row on
// newlines, drop preamble headers and blanks, trim. THROWN sentinel is preserved.
function normalizeFindings(rows: string[]): string[] {
  const lines: string[] = [];
  for (const raw of rows) {
    if (raw === THROWN) { lines.push(THROWN); continue; }
    for (const part of String(raw ?? '').split('\n')) {
      const t = part.trim();
      if (!t) continue;
      if (PREAMBLE_RE.test(t)) continue;
      lines.push(t);
    }
  }
  return lines;
}

function classify(rows: string[]): IntegrityResult {
  const lines = normalizeFindings(rows);

  if (lines.length === 1 && lines[0] === 'ok') {
    return { cls: IntegrityClass.OK, rows, findings: [] };
  }
  if (lines.length === 0) {
    // check produced nothing (or only preamble) — cannot affirm 'ok'; fatal.
    return { cls: IntegrityClass.FATAL, rows, findings: ['<no integrity_check finding lines>'] };
  }
  if (lines.includes(THROWN)) {
    return { cls: IntegrityClass.FATAL, rows, findings: [THROWN] };
  }

  let hasFatal = false;
  let hasIndex = false;
  const findings: string[] = [];
  for (const line of lines) {
    if (line === 'ok') continue; // 'ok' can co-occur; only findings matter
    if (BENIGN_RE.test(line)) continue; // benign, contributes nothing to escalation
    findings.push(line);
    if (INDEX_RE.test(line)) {
      hasIndex = true;
    } else {
      hasFatal = true; // unrecognized / bare-malformed / anything else => fatal
    }
  }

  if (hasFatal) return { cls: IntegrityClass.FATAL, rows, findings };
  if (hasIndex) return { cls: IntegrityClass.INDEX, rows, findings };
  return { cls: IntegrityClass.BENIGN, rows, findings: [] };
}

// Sentinel a caller pushes into the rows array when the PRAGMA itself threw.
const THROWN = '__integrity_check_threw__';

export const classifyIntegrity = Object.assign(classify, { THROWN });
