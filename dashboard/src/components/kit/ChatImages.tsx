/**
 * Images and video in the chat: the thumbnails under a message and the full-screen view.
 *
 *   ImageStrip   the message's media as fixed-size tiles (lib/imageRefs THUMB): the box is sized
 *                before the bytes arrive and never changes, so the thread does not shift; the
 *                bytes load lazily; a refusal (404) is a small chip, never the browser's broken
 *                image glyph. A video is a poster-less <video controls playsinline muted> in the
 *                same box (it plays right there; the corner button opens it large), never autoplay.
 *                It is lazy: no source and preload="none" until the tile comes within NEAR_PX of
 *                the thread's scrolling container's visible area (lib/nearViewport.ts), then
 *                preload="metadata", so a window full of videos fetches nothing for the ones far
 *                off screen. Only media the server stamped is drawn: the client never sends a path
 *                and never asks for an id.
 *   ImageOverlay a tap: the image (or the video, playing) front and centre over a frosted, blurred
 *                backdrop; top right Share, Download, Close. Share sends the FILE through the Web
 *                Share API where the browser can share files, else it downloads (the tooltip says
 *                so): the URL needs this install's sign-in, so it is never what is shared
 *                (lib/mediaShare.ts). Escape closes, so does a click outside the media. The page
 *                keeps its width while the overlay is up (the scrollbar's width is held as
 *                padding, so nothing shifts).
 *
 * Every surface shares these: the agent chat in Bubbles and Modern (and Dev), Arturo's page and
 * his floating pane. Colours are the --bui-* tokens, set for light and dark; `dark` forces the
 * dark look (the pane is dark in either theme, and the overlay is drawn on the page's body).
 */
import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { Download, ImageOff, Maximize2, Share2, X } from 'lucide-react';
import { mediaUrl, THUMB, type ChatImage } from '../../lib/imageRefs';
import { canShareFiles, mediaTypeFor, shareMedia, SHARE_FALLBACK_TITLE, SHARE_TITLE } from '../../lib/mediaShare';
import { watchNear } from '../../lib/nearViewport';
import './chatImages.css';

function ErrorChip({ img, box }: { img: ChatImage; box: { width: number; height: number } }) {
  return (
    <div className="chat-image chat-image-error" style={box} title={img.path} data-chat-image="error">
      <ImageOff size={14} aria-hidden />
      <span className="chat-image-name">{img.name}</span>
      <span className="chat-image-note">couldn't load</span>
    </div>
  );
}

/** A video in the thread: it plays in its tile (muted until the person turns the sound on, never by itself); the
 *  corner opens it large. Nothing is fetched until the tile is near the thread's visible area (lib/nearViewport). Where
 *  the browser has no IntersectionObserver it loads at once (metadata only), as it did before. */
function VideoThumb({ img, onOpen }: { img: ChatImage; onOpen: (img: ChatImage) => void }) {
  const url = mediaUrl(img.id);
  const ref = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [errored, setErrored] = useState<string | null>(null);
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const el = boxRef.current;
    if (near || !el) return;
    return watchNear(el, () => setNear(true));
  }, [near]);
  const box = { width: THUMB.width, height: THUMB.height };
  if (!url || errored === url) return <ErrorChip img={img} box={box} />;
  return (
    <div ref={boxRef} className="chat-image chat-video is-loaded" style={box} title={img.name} data-chat-image="video" data-near={near ? 'yes' : 'no'}>
      <video ref={ref} src={near ? url : undefined} controls preload={near ? 'metadata' : 'none'} playsInline muted aria-label={img.name}
             onError={() => setErrored(url)} />
      <button type="button" className="chat-video-open" aria-label={`Open ${img.name}`} title="Open large"
              onClick={() => { ref.current?.pause(); onOpen(img); }}>
        <Maximize2 size={14} aria-hidden />
      </button>
    </div>
  );
}

function Thumb({ img, onOpen }: { img: ChatImage; onOpen: (img: ChatImage) => void }) {
  const url = mediaUrl(img.id);
  // Load state is kept per URL, so a new id starts over without an effect.
  const [loaded, setLoaded] = useState<string | null>(null);
  const [errored, setErrored] = useState<string | null>(null);
  const state: 'loading' | 'ok' | 'error' = !url || errored === url ? 'error' : loaded === url ? 'ok' : 'loading';
  const box = { width: THUMB.width, height: THUMB.height };
  if (state === 'error') return <ErrorChip img={img} box={box} />;
  return (
    <button type="button" className={`chat-image${state === 'ok' ? ' is-loaded' : ''}`} style={box} title={img.name}
            aria-label={`Open ${img.name}`} data-chat-image={state} disabled={state !== 'ok'}
            onClick={() => onOpen(img)}>
      <img src={url!} alt={img.name} loading="lazy" decoding="async" draggable={false}
           onLoad={() => setLoaded(url!)} onError={() => setErrored(url!)} />
      <span className="chat-image-caption">{img.name}</span>
    </button>
  );
}

/** The images under a message. Nothing to show, nothing drawn (no empty row). */
export function ImageStrip({ images, dark = false, className = '' }: { images?: ChatImage[]; dark?: boolean; className?: string }) {
  const [open, setOpen] = useState<ChatImage | null>(null);
  if (!images || images.length === 0) return null;
  return (
    <>
      <div className={`chat-images ${className}`.trim()} data-chat-images={images.length}>
        {images.map((img) => (img.kind === 'video'
          ? <VideoThumb key={img.id} img={img} onOpen={setOpen} />
          : <Thumb key={img.id} img={img} onOpen={setOpen} />))}
      </div>
      {open && <ImageOverlay img={open} dark={dark} onClose={() => setOpen(null)} />}
    </>
  );
}

/** Save the file the ordinary way: the route's attachment answer, through a link the page clicks (no URL is copied). */
function downloadFile(img: ChatImage) {
  const href = mediaUrl(img.id, true);
  if (!href) return;
  const a = document.createElement('a');
  a.href = href;
  a.download = img.name;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function ImageOverlay({ img, dark = false, onClose }: { img: ChatImage; dark?: boolean; onClose: () => void }) {
  const url = mediaUrl(img.id) ?? '';
  const closeRef = useRef<HTMLButtonElement>(null);
  const video = img.kind === 'video';
  const type = mediaTypeFor(img.name, img.kind);
  const nav = typeof navigator === 'undefined' ? undefined : navigator;
  const filesOk = canShareFiles(nav, type);
  // An image is fetched as the overlay opens (the browser already holds it), so a tap shares at once and keeps the
  // tap's permission; a video (up to 256 MiB) is fetched only when Share is tapped.
  const ready = useRef<Blob | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    ready.current = null;
    if (!filesOk || video || !url) return;
    let live = true;
    fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.blob() : null)).then((b) => { if (live) ready.current = b; }).catch(() => { /* fetched at the tap */ });
    return () => { live = false; };
  }, [url, filesOk, video]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    document.addEventListener('keydown', onKey, true);
    closeRef.current?.focus();
    // Hold the page still: the scrollbar's width becomes padding while scrolling is off.
    const body = document.body;
    const prev = { overflow: body.style.overflow, paddingRight: body.style.paddingRight };
    const gutter = window.innerWidth - document.documentElement.clientWidth;
    body.style.overflow = 'hidden';
    if (gutter > 0) body.style.paddingRight = `${gutter}px`;
    return () => {
      document.removeEventListener('keydown', onKey, true);
      body.style.overflow = prev.overflow;
      body.style.paddingRight = prev.paddingRight;
    };
  }, [onClose]);
  const onBackdrop = (e: MouseEvent) => { if (e.target === e.currentTarget) onClose(); };
  const doShare = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await shareMedia(url, img.name, type, {
        nav,
        ready: ready.current,
        fetchBlob: async (u) => { const r = await fetch(u, { credentials: 'same-origin' }); if (!r.ok) throw new Error(String(r.status)); return r.blob(); },
        download: () => downloadFile(img),
      });
    } finally { setBusy(false); }
  };
  return createPortal(
    <div className={`chat-image-overlay${dark ? ' dark' : ''}`} role="dialog" aria-modal="true" aria-label={img.name} onClick={onBackdrop} data-chat-image-overlay>
      <div className="chat-image-overlay-bar" onClick={(e) => e.stopPropagation()}>
        <span className="chat-image-overlay-title" title={img.path}>{img.name}</span>
        <span className="chat-image-overlay-tools">
          <button type="button" className="chat-image-overlay-btn" onClick={() => void doShare()} disabled={busy}
                  aria-label="Share" title={filesOk ? SHARE_TITLE : SHARE_FALLBACK_TITLE} data-share={filesOk ? 'files' : 'download'}>
            <Share2 size={18} />
          </button>
          <a className="chat-image-overlay-btn" href={mediaUrl(img.id, true) ?? '#'} download={img.name} aria-label="Download" title="Download">
            <Download size={18} />
          </a>
          <button ref={closeRef} type="button" className="chat-image-overlay-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <X size={20} />
          </button>
        </span>
      </div>
      <div className="chat-image-overlay-stage" onClick={onBackdrop}>
        {video
          ? <video src={url} className="chat-image-overlay-img chat-image-overlay-video" controls autoPlay playsInline aria-label={img.name}
                   onClick={(e) => e.stopPropagation()} />
          : <img src={url} alt={img.name} className="chat-image-overlay-img" draggable={false} onClick={(e) => e.stopPropagation()} />}
      </div>
    </div>,
    document.body,
  );
}
