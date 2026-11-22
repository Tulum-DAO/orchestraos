/**
 * Images in the chat: the thumbnails under a message and the full-screen view.
 *
 *   ImageStrip   the message's images as fixed-size thumbnails (lib/imageRefs THUMB): the box is
 *                sized before the bytes arrive and never changes, so the thread does not shift;
 *                the bytes load lazily; a refusal (404) is a small chip, never the browser's broken
 *                image glyph. An image the server did not stamp (Arturo) asks for its id first.
 *   ImageOverlay a tap: the image front and centre over a frosted, blurred backdrop; top right
 *                Share (only when the browser offers the Web Share API), Download, Close. Escape
 *                closes, so does a click outside the image.
 *
 * Every surface shares these: the agent chat in Bubbles and Modern (and Dev), Arturo's page and
 * his floating pane. Colours are the --bui-* tokens, set for light and dark.
 */
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { Download, ImageOff, Share2, X } from 'lucide-react';
import { mediaUrl, THUMB, type ChatImage } from '../../lib/imageRefs';
import { resolveMediaId } from '../../lib/mediaResolve';
import './chatImages.css';

/** The id for an image: the server's, or asked for by (agent, path) when it came without one. */
function useImageId(img: ChatImage, agent?: string): { id: string | null; pending: boolean } {
  const need = !img.id && !!agent;
  const key = `${agent ?? ''}\0${img.path}`;
  const [res, setRes] = useState<{ key: string; id: string | null } | null>(null);
  useEffect(() => {
    if (!need) return;
    let on = true;
    void resolveMediaId(agent!, img.path).then((id) => { if (on) setRes({ key, id }); });
    return () => { on = false; };
  }, [need, agent, img.path, key]);
  if (img.id) return { id: img.id, pending: false };
  if (!agent) return { id: null, pending: false };
  if (res && res.key === key) return { id: res.id, pending: false };
  return { id: null, pending: true };
}

function Thumb({ img, agent, onOpen }: { img: ChatImage; agent?: string; onOpen: (img: ChatImage, id: string) => void }) {
  const { id, pending } = useImageId(img, agent);
  const url = id ? mediaUrl(id) : null;
  // Load state is kept per URL, so a new id starts over without an effect.
  const [loaded, setLoaded] = useState<string | null>(null);
  const [errored, setErrored] = useState<string | null>(null);
  const state: 'loading' | 'ok' | 'error' = url && errored === url ? 'error' : url && loaded === url ? 'ok' : 'loading';
  const failed = state === 'error' || (!pending && !url);
  const box = { width: THUMB.width, height: THUMB.height };
  if (failed) {
    return (
      <div className="chat-image chat-image-error" style={box} title={img.path} data-chat-image="error">
        <ImageOff size={14} aria-hidden />
        <span className="chat-image-name">{img.name}</span>
        <span className="chat-image-note">couldn't load</span>
      </div>
    );
  }
  return (
    <button type="button" className={`chat-image${state === 'ok' ? ' is-loaded' : ''}`} style={box} title={img.name}
            aria-label={`Open ${img.name}`} data-chat-image={state} disabled={!url}
            onClick={() => { if (url && id) onOpen(img, id); }}>
      {url && (
        <img src={url} alt={img.name} loading="lazy" decoding="async" draggable={false}
             onLoad={() => setLoaded(url)} onError={() => setErrored(url)} />
      )}
      <span className="chat-image-caption">{img.name}</span>
    </button>
  );
}

/** The images under a message. Nothing to show, nothing drawn (no empty row). */
export function ImageStrip({ images, agent, className = '' }: { images?: ChatImage[]; agent?: string; className?: string }) {
  const [open, setOpen] = useState<{ img: ChatImage; id: string } | null>(null);
  if (!images || images.length === 0) return null;
  return (
    <>
      <div className={`chat-images ${className}`.trim()} data-chat-images={images.length}>
        {images.map((img) => <Thumb key={img.path} img={img} agent={agent} onOpen={(i, id) => setOpen({ img: i, id })} />)}
      </div>
      {open && <ImageOverlay img={open.img} id={open.id} onClose={() => setOpen(null)} />}
    </>
  );
}

/** Whether this browser can share a URL (the Web Share API, with no file to attach). */
function canShare(nav: Pick<Navigator, 'share' | 'canShare'> | undefined = typeof navigator === 'undefined' ? undefined : navigator): boolean {
  if (!nav || typeof nav.share !== 'function') return false;
  try { return typeof nav.canShare !== 'function' || nav.canShare({ url: 'https://example.invalid/' }); } catch { return false; }
}

export function ImageOverlay({ img, id, onClose }: { img: ChatImage; id: string; onClose: () => void }) {
  const url = mediaUrl(id)!;
  const closeRef = useRef<HTMLButtonElement>(null);
  const share = canShare();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    document.addEventListener('keydown', onKey, true);
    closeRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onKey, true); document.body.style.overflow = prev; };
  }, [onClose]);
  const onBackdrop = (e: MouseEvent) => { if (e.target === e.currentTarget) onClose(); };
  const doShare = async () => {
    try { await navigator.share({ title: img.name, url: new URL(url, window.location.href).toString() }); } catch { /* the person closed the sheet */ }
  };
  return createPortal(
    <div className="chat-image-overlay" role="dialog" aria-modal="true" aria-label={img.name} onClick={onBackdrop} data-chat-image-overlay>
      <div className="chat-image-overlay-bar" onClick={(e) => e.stopPropagation()}>
        <span className="chat-image-overlay-title" title={img.path}>{img.name}</span>
        <span className="chat-image-overlay-tools">
          {share && (
            <button type="button" className="chat-image-overlay-btn" onClick={() => void doShare()} aria-label="Share" title="Share">
              <Share2 size={18} />
            </button>
          )}
          <a className="chat-image-overlay-btn" href={mediaUrl(id, true)!} download={img.name} aria-label="Download" title="Download">
            <Download size={18} />
          </a>
          <button ref={closeRef} type="button" className="chat-image-overlay-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={20} />
          </button>
        </span>
      </div>
      <div className="chat-image-overlay-stage" onClick={onBackdrop}>
        <img src={url} alt={img.name} className="chat-image-overlay-img" draggable={false} onClick={(e) => e.stopPropagation()} />
      </div>
    </div>,
    document.body,
  );
}
