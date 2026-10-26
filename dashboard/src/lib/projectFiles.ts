import type { ProjectFileEntry } from './api';

/** Files grouped under their folder, folders in order, root first. */
export function groupByFolder(files: ProjectFileEntry[]): [string, ProjectFileEntry[]][] {
  const groups = new Map<string, ProjectFileEntry[]>();
  for (const f of files) {
    const i = f.path.lastIndexOf('/');
    const dir = i < 0 ? '' : f.path.slice(0, i);
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(f);
  }
  return [...groups.entries()].sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)));
}

/** How a text file is shown in its window. */
export type TextView = 'markdown' | 'html' | 'csv' | 'code';
export function textViewFor(path: string): TextView {
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase();
  if (ext === 'md' || ext === 'markdown') return 'markdown';
  if (ext === 'html' || ext === 'htm') return 'html';
  if (ext === 'csv') return 'csv';
  return 'code';
}

/** A CSV as rows of cells (quoted fields, "" escapes, commas and newlines inside quotes). Capped. */
export function parseCsv(text: string, maxRows = 500): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cell); rows.push(row); row = []; cell = '';
      if (rows.length >= maxRows) return rows;
    } else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

/** Where the nth open window starts: cascaded, inside the viewport. */
export function cascade(n: number, vw: number, vh: number, w = 640, h = 480): { x: number; y: number } {
  const step = 28, slots = 8;
  const k = n % slots;
  return { x: Math.max(8, Math.min(vw - w - 8, 120 + k * step)), y: Math.max(8, Math.min(vh - h - 8, 90 + k * step)) };
}

/** What Arturo is told is in front of the operator: the top window's file, else the project. */
export function focusFor(slug: string, name: string, folder: string | undefined, top: string | null) {
  if (top && folder) return { kind: 'file', id: `${folder}/${top}`, label: `${name}: ${top}` };
  return { kind: 'project', id: slug, label: name };
}
