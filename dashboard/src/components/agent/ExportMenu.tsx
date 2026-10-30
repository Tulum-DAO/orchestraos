/**
 * Export this agent's conversation as Markdown: download the file or copy it. One small icon in the
 * agent page's top bar (the bar is kept lean so the seat name fits on a phone). The server redacts
 * secrets and leaves out the model's thinking (api/src/lib/transcriptMarkdown.ts).
 */
import { useEffect, useRef, useState } from 'react';
import { FileDown } from 'lucide-react';
import { exportFileName, exportUrl } from '../../lib/exportMarkdown';

export function ExportMenu({ agentId }: { agentId: string }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  async function fetchMd(): Promise<{ text: string; name: string }> {
    const r = await fetch(exportUrl(agentId));
    if (!r.ok) throw new Error((await r.text().catch(() => '')) || `export failed (${r.status})`);
    return { text: await r.text(), name: exportFileName(r.headers.get('Content-Disposition'), agentId) };
  }

  async function download() {
    setNote(null);
    try {
      const { text, name } = await fetchMd();
      const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }));
      const a = document.createElement('a');
      a.href = url; a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setOpen(false);
    } catch (e) { setNote(String((e as Error).message || e)); }
  }

  async function copy() {
    setNote(null);
    try {
      const { text } = await fetchMd();
      await navigator.clipboard.writeText(text);
      setNote('Copied');
      setTimeout(() => { setNote(null); setOpen(false); }, 1200);
    } catch (e) { setNote(String((e as Error).message || e)); }
  }

  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-label="Export conversation" title="Export conversation"
              aria-expanded={open} className="p-2 text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg">
        <FileDown size={18} />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 rounded-md border border-border bg-background shadow-lg z-30 py-1 text-sm" role="menu">
          <button type="button" role="menuitem" onClick={download} className="w-full text-left px-3 py-2 hover:bg-muted">Download .md</button>
          <button type="button" role="menuitem" onClick={copy} className="w-full text-left px-3 py-2 hover:bg-muted">Copy as Markdown</button>
          <p className="px-3 pt-1 pb-2 text-[11px] text-muted-foreground">Last 50 turns. Secrets removed; thinking left out.</p>
          {note && <p className="px-3 pb-2 text-[11px] text-tone-amber-300" role="status">{note}</p>}
        </div>
      )}
    </div>
  );
}
