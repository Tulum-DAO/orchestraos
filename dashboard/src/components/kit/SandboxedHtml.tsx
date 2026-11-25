import { useEffect, useRef, useState } from 'react';
import { SANDBOX, answerBridge, sandboxedPage } from '../../lib/sandboxedHtml';

/**
 * Agent-written HTML (a questionnaire, a proposal's one-pager) in a frame with no dashboard origin (lib/sandboxedHtml):
 * the page is read as text and framed as srcdoc, sandboxed, with its own policy and shim first. A questionnaire
 * (`questionnaireId`) gets the fetch bridge: its answers and feedback posts are sent from here, nothing else is.
 */
export default function SandboxedHtml({ src, title, minHeight, questionnaireId }: {
  src: string;
  title: string;
  minHeight: string;
  questionnaireId?: string;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [page, setPage] = useState<{ src: string; doc: string | null; err: string | null }>({ src, doc: null, err: null });
  useEffect(() => {
    let off = false;
    fetch(src)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((text) => { if (!off) setPage({ src, doc: sandboxedPage(text, { bridge: !!questionnaireId }), err: null }); })
      .catch((e: unknown) => { if (!off) setPage({ src, doc: null, err: e instanceof Error ? e.message : String(e) }); });
    return () => { off = true; };
  }, [src, questionnaireId]);
  useEffect(() => {
    if (!questionnaireId) return;
    const onMessage = (e: MessageEvent) => {
      // Only this frame's own page: another frame or window cannot borrow the bridge.
      const win = ref.current?.contentWindow;
      if (!win || e.source !== win) return;
      void answerBridge(e.data, questionnaireId, (url, init) => fetch(url, init))?.then((reply) => win.postMessage(reply, '*'));
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [questionnaireId]);
  const doc = page.src === src ? page.doc : null;
  const err = page.src === src ? page.err : null;
  if (err) return <p className="text-xs text-tone-500 p-3">Could not load this page ({err}).</p>;
  if (doc == null) return <p className="text-xs text-tone-500 p-3">Loading…</p>;
  return <iframe ref={ref} sandbox={SANDBOX} srcDoc={doc} referrerPolicy="no-referrer" className="w-full border-0" style={{ minHeight }} title={title} />;
}
