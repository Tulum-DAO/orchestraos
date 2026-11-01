/**
 * ExportChatButton — "Export chat" in the top-right corner of an agent's slideout (the gear's panel), just under its
 * Done (phone) or X (desktop). It opens a popover menu anchored to it, floating over the panel: Download .md or Copy
 * as Markdown, the same export the top bar's button once did (GET /api/agents/:id/transcript.md; the server redacts
 * secrets and leaves out the model's thinking). The menu closes on a choice, an outside click or Esc. It takes no
 * room of its own: a zero-height anchor at the top of the panel body, so the avatar and name stay where they are.
 */
import { useEffect, useRef, useState } from 'react';
import { FileDown } from 'lucide-react';
import { exportFileName, exportUrl } from '../../lib/exportMarkdown';
import './exportChat.css';

export function ExportChatButton({ agentId }: { agentId: string }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } };
    document.addEventListener('pointerdown', down);
    document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('pointerdown', down); document.removeEventListener('keydown', key, true); };
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
    <div className="export-chat-anchor">
      <div className="export-chat" ref={box}>
        <button type="button" className="export-chat-btn" aria-haspopup="menu" aria-expanded={open} data-export-chat
                onClick={() => { setOpen((v) => !v); setNote(null); }}>
          <FileDown size={14} aria-hidden /> Export chat
        </button>
        {open && (
          <div className="export-chat-menu" role="menu" aria-label="Export chat">
            <button type="button" role="menuitem" onClick={() => void download()} data-export="download">Download .md</button>
            <button type="button" role="menuitem" onClick={() => void copy()} data-export="copy">Copy as Markdown</button>
            <p className="export-chat-note">Last 50 turns. Secrets removed; thinking left out.</p>
            {note && <p className="export-chat-note" role="status">{note}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
