/**
 * tomlEdit.ts — one string key in one [section] of orchestra.toml, read or set IN PLACE: every other line (comments,
 * order, blank lines, other sections) is left exactly as it was, because people edit this file by hand (Settings
 * phase 1: General › Time zone writes [operator] timezone). Only `key = value` lines directly in the section count.
 */
import fs from 'fs';
import * as toml from 'toml';

const headerOf = (line: string): string | null => /^\s*\[([^\]]+)\]/.exec(line)?.[1]?.trim() ?? null;

function sectionBounds(lines: string[], section: string): [number, number] | null {
  const start = lines.findIndex((l) => headerOf(l) === section);
  if (start < 0) return null;
  let end = start + 1;
  while (end < lines.length && headerOf(lines[end]) === null) end++;
  return [start, end];
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// prefix ("key = "), the value (a basic "string", a 'literal' or a bare token), the rest (spaces, an inline comment)
const keyRe = (key: string) => new RegExp(`^(\\s*${escapeRe(key)}\\s*=\\s*)("(?:[^"\\\\]|\\\\.)*"|'[^']*'|[^\\s#]*)(.*)$`);

export function readTomlString(text: string, section: string, key: string): string | null {
  const lines = text.split('\n');
  const b = sectionBounds(lines, section);
  if (!b) return null;
  const re = keyRe(key);
  for (let i = b[0] + 1; i < b[1]; i++) {
    const m = re.exec(lines[i]);
    if (!m) continue;
    const v = m[2];
    if (v.startsWith('"')) { try { return JSON.parse(v) as string; } catch { return null; } }
    if (v.startsWith("'")) return v.slice(1, -1);
    return v;
  }
  return null;
}

export function setTomlString(text: string, section: string, key: string, value: string): string {
  const lines = text.split('\n');
  const v = JSON.stringify(value);
  const b = sectionBounds(lines, section);
  if (!b) {
    const base = text.endsWith('\n') ? text : `${text}\n`;
    return `${base}\n[${section}]\n${key} = ${v}\n`;
  }
  const re = keyRe(key);
  for (let i = b[0] + 1; i < b[1]; i++) {
    const m = re.exec(lines[i]);
    if (m) { lines[i] = `${m[1]}${v}${m[3]}`; return lines.join('\n'); }
  }
  let at = b[1];
  while (at - 1 > b[0] && lines[at - 1].trim() === '') at--;
  lines.splice(at, 0, `${key} = ${v}`);
  return lines.join('\n');
}

/** setTomlString, then proof: the new text is parsed with the TOML parser the API reads orchestra.toml with, and must
 *  hold exactly `value` at [section] key. The line editor above only knows `key = "value"` directly under a [section]
 *  header; a root dotted key (`operator.id = …`), an inline table (`operator = {…}`), a quoted key (`"timezone" = …`) or a
 *  triple-quoted value would come out broken or duplicated. Throws (nothing written) instead. */
export function setTomlStringChecked(text: string, section: string, key: string, value: string): string {
  const out = setTomlString(text, section, key, value);
  let got: unknown;
  try { got = (toml.parse(out) as any)?.[section]?.[key]; } catch { got = undefined; }
  if (got !== value) throw new Error(`Couldn't edit orchestra.toml safely; set [${section}] ${key} by hand`);
  return out;
}

/** An IANA zone this runtime knows, or "" (unset: times are shown in UTC). */
export function isTimeZone(v: unknown): v is string {
  if (v === '') return true;
  if (typeof v !== 'string' || !/^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/.test(v)) return false;
  try { new Intl.DateTimeFormat('en-US', { timeZone: v }); return true; } catch { return false; }
}

/** Atomic replace that keeps the file's mode (and writes through a symlink to its target). */
export function writeFileKeepingMode(file: string, text: string): void {
  let real = file;
  try { real = fs.realpathSync(file); } catch { /* new file */ }
  let mode = 0o644;
  try { mode = fs.statSync(real).mode & 0o777; } catch { /* new file */ }
  const tmp = `${real}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text, { mode });
  fs.chmodSync(tmp, mode);
  fs.renameSync(tmp, real);
}
