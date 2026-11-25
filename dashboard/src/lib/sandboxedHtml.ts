/**
 * sandboxedHtml — agent-written HTML (a questionnaire, a proposal's one-pager) drawn in the dashboard without the
 * dashboard's origin. Before, the page was framed same-origin from /api/…/html with no sandbox: a script in it ran as
 * the dashboard, with the operator's session.
 *
 * Now (components/kit/SandboxedHtml.tsx) the parent reads the page as text and frames it as srcdoc in an iframe
 * sandboxed "allow-scripts allow-forms" (never allow-same-origin: its origin is opaque, so it reads no cookie, no
 * storage and no dashboard state), with its own Content-Security-Policy first in its head (connect-src 'none' through
 * default-src: a sandboxed frame could still SEND a blind request, and the tailnet adds the operator's identity to
 * it, so the policy is what stops every fetch, XHR, beacon, form post and image ping).
 *
 * Its answers still save: a shim placed before the page's own scripts replaces window.fetch with a request to the
 * parent (postMessage). The parent sends ONLY what a questionnaire is for (bridgeTarget): a POST to
 * /api/learning/feedback, or to /api/questionnaires/<this questionnaire>/submit. Anything else is answered 403 here
 * and never reaches the API. The pages build their URL many ways (window.location.origin + '/api', a hard-coded host,
 * a bare path; in the sandbox location.origin is "null"), so the parent reads the PATH alone.
 *
 * Pages that keep a draft in localStorage would stop at the first read (an opaque origin's storage throws), so the
 * shim also gives them an in-memory stand-in: the draft lives as long as the frame.
 */

/** The frame's own policy, the API's header for these pages: inline script and style (the pages are single files),
 *  Google Fonts (three pages use them), data: images only (a remote image is a GET the page controls); nothing fetched (connect-src 'none'), no form
 *  posted, no base, no nested frame. */
export const FRAME_POLICY = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
  + "img-src data:; font-src https://fonts.gstatic.com data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'";

export const SANDBOX = 'allow-scripts allow-forms';

/** The message a framed page sends for a fetch; the answer is `${BRIDGE}:reply`. */
export const BRIDGE = 'orchestra:fetch';

/** In-memory localStorage / sessionStorage, put in place only where the real one throws. */
const STORAGE = `(function(){function S(){var m={},k=function(){return Object.keys(m)};return{getItem:function(x){x=String(x);return Object.prototype.hasOwnProperty.call(m,x)?m[x]:null},setItem:function(x,v){m[String(x)]=String(v)},removeItem:function(x){delete m[String(x)]},clear:function(){m={}},key:function(i){var a=k();return i<a.length?a[i]:null},get length(){return k().length}}}['localStorage','sessionStorage'].forEach(function(n){try{var s=window[n];if(s)return}catch(e){}try{Object.defineProperty(window,n,{value:S(),configurable:true})}catch(e){}})})();`;

/** The page's window.fetch, as the shim replaces it: a request to the parent, answered as a Response. Strings only,
 *  bodies as text (the pages post JSON). */
const FETCH = `(function(){var n=0,w={};addEventListener('message',function(e){var d=e.data;if(e.source!==parent||!d||d.type!=='${BRIDGE}:reply'||!w[d.id])return;var p=w[d.id];delete w[d.id];p(new Response(d.body,{status:d.status,headers:{'Content-Type':d.contentType||'application/json'}}));});window.fetch=function(u,o){o=o||{};var id=++n,url=typeof u==='string'?u:(u&&u.url)||String(u),b=o.body;return new Promise(function(r,j){if(b!=null&&typeof b!=='string'){j(new TypeError('only text bodies are sent'));return;}w[id]=r;parent.postMessage({type:'${BRIDGE}',id:id,url:url,method:String(o.method||'GET').toUpperCase(),body:b==null?null:b},'*');});};})();`;

/** The page as the frame gets it: its policy and the shim first, then the page whole. */
export function sandboxedPage(html: string, opts: { bridge: boolean }): string {
  const head = `<meta http-equiv="Content-Security-Policy" content="${FRAME_POLICY}"><meta name="referrer" content="no-referrer">`
    + `<script>${STORAGE}${opts.bridge ? FETCH : ''}</script>`;
  // Before anything of the page's own, even a doctype-less fragment: a <meta> CSP only governs what comes after it.
  return `<!doctype html>${head}${html.replace(/^\s*<!doctype[^>]*>/i, '')}`;
}

/** Where a framed questionnaire's request may go: the API path to send it to, or null (refused, never sent). */
export function bridgeTarget(url: unknown, method: unknown, questionnaireId: string | undefined): string | null {
  if (typeof url !== 'string' || String(method).toUpperCase() !== 'POST' || !questionnaireId) return null;
  // The path alone: whatever host the page named (its "origin" is "null" in the sandbox).
  const path = url.replace(/^[a-z][\w+.-]*:\/\/[^/]+/i, '').replace(/^null(?=\/)/, '').split('#')[0];
  const [p, query = ''] = path.split('?');
  if (/^\/?(?:api\/)?learning\/feedback$/.test(p)) return `/api/learning/feedback${query ? `?${query}` : ''}`;
  const m = /^\/?(?:api\/)?questionnaires\/([^/]+)\/submit$/.exec(p);
  if (m) {
    let id = '';
    try { id = decodeURIComponent(m[1]); } catch { return null; }
    // Its own submit only: never another questionnaire's.
    if (id === questionnaireId) return `/api/questionnaires/${encodeURIComponent(id)}/submit`;
  }
  return null;
}

export interface BridgeReply { type: string; id: unknown; status: number; body: string; contentType: string }

/** The parent's answer to a message from the frame: null when it is not a bridged request (ignored); otherwise the
 *  reply to post back. Only bridgeTarget's requests are sent, as JSON posts; the rest are answered here. */
export function answerBridge(data: unknown, questionnaireId: string | undefined,
  doFetch: (url: string, init: RequestInit) => Promise<Response>): Promise<BridgeReply> | null {
  if (!data || typeof data !== 'object' || (data as { type?: unknown }).type !== BRIDGE) return null;
  const { id, url, method, body } = data as { id?: unknown; url?: unknown; method?: unknown; body?: unknown };
  const reply = (status: number, text: string, contentType = 'application/json'): BridgeReply =>
    ({ type: `${BRIDGE}:reply`, id, status, body: text, contentType });
  const target = bridgeTarget(url, method, questionnaireId);
  if (!target) return Promise.resolve(reply(403, '{"error":"not allowed from this page"}'));
  if (body != null && typeof body !== 'string') return Promise.resolve(reply(400, '{"error":"text bodies only"}'));
  return doFetch(target, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ?? undefined })
    .then(async (r) => reply(r.status, await r.text(), r.headers.get('Content-Type') || 'application/json'))
    .catch(() => reply(502, '{"error":"could not reach the dashboard API"}'));
}
