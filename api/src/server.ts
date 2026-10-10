import './lib/configExit.js';   // FIRST: turns a missing orchestra.toml into one plain line (S5)
import 'dotenv/config';
import { queryDb } from './lib/db.js';
import express from 'express';
import cors from 'cors';
import { createServer, type IncomingMessage } from 'http';
import { join } from 'path';
import { WebSocketServer } from 'ws';

import agentsRouter from './routes/agents.js';
import redAlertRouter from './routes/red-alert.js';
import chatTranscriptRouter from './routes/chat-transcript.js';
import transcriptStreamRouter from './routes/transcript-stream.js';
import tasksRouter from './routes/tasks.js';
import activityRouter from './routes/activity.js';
import systemRouter from './routes/system.js';
import memoryRouter from './routes/memory.js';
import roadmapsRouter from './routes/roadmaps.js';
import voiceRouter from './routes/voice.js';
import arturoRouter from './routes/arturo.js';
import agentsNewRouter from './routes/agents-new.js';
import approvalsRouter from './routes/approvals.js';
import analyticsRouter from './routes/analytics.js';
import workflowsRouter from './routes/workflows.js';
import skillsRouter from './routes/skills.js';
import transitRouter from './routes/transit.js';
import projectsRouter from './routes/projects.js';
import authRouter from './routes/auth.js';
import uploadsRouter from './routes/uploads.js';
import paneUrlRouter from './routes/pane-url.js';
import adaptiveRouter from './routes/adaptive.js';
import messagesRouter from './routes/messages.js';
import machinesRouter from './routes/machines.js';
import signingRouter from './routes/signing.js';
import learningRouter from './routes/learning.js';
import questionnairesRouter from './routes/questionnaires.js';
import unifiedApprovalsRouter from './routes/unified-approvals.js';
import tokenAuthRouter from './routes/token-auth.js';
import tasksV2Router from './routes/tasks-v2.js';
import northStarsRouter from './routes/northstars.js';
import peopleRouter from './routes/people.js';
import agentStateRouter from './routes/agent-state.js';
import inspectFeedbackRouter from './routes/inspect-feedback.js';
import telemetryRouter from './routes/telemetry.js';
import agentSendRouter from './routes/agent-send.js';
import runtimesAvailableRouter from './routes/runtimes-available.js';
import factsRouter from './routes/facts.js';
import chatHistoryRouter from './routes/chat-history.js';
import { initActivityStream } from './services/activity-stream.js';
import { initStatusStream, addStatusSSEClient } from './services/status-stream.js';
import { setupTerminalWebSocket } from './routes/terminal.js';
import { setupVoiceLiveWebSocket } from './routes/voice-live.js';
import { loadConfig } from './lib/config.js';
import { principal } from './lib/principal.js';
import { accessLog } from './lib/access-log.js';
import { originDecision, publicOrigins } from './lib/cors-origin.js';
import { wsOriginDecision } from './lib/ws-origin.js';
import { capabilities } from './lib/capabilities.js';

const app = express();

const corsAllowlist = (): string[] => {
  const cfg = loadConfig();
  const extra = (process.env.ORCHESTRA_API_CORS_ORIGINS || '')
    .split(',').map(s => s.trim()).filter(Boolean);
  const hosts = cfg.dashboardHost === '0.0.0.0' || cfg.dashboardHost === '::'
    ? ['127.0.0.1', 'localhost']
    : [cfg.dashboardHost, cfg.dashboardHost === '127.0.0.1' ? 'localhost' : cfg.dashboardHost];
  const origins = new Set<string>([...extra, ...publicOrigins(cfg.publicHost)]);
  for (const h of hosts) {
    origins.add(`http://${h}:${cfg.dashboardPort}`);
    origins.add(`https://${h}:${cfg.dashboardPort}`);
  }
  return [...origins];
};

// One line per distinct origin. This fired on EVERY request from the operator's own
// dashboard, so a real signal would have been buried in copies of a false alarm.
const warnedOrigins = new Set<string>();

// CORS: an explicit allowlist, not `cors()` — the bare call reflects ANY origin, so any
// page the operator visited could read this API from their browser. Allowed: the
// dashboard's own origin (both loopback spellings), the install's externally-reachable
// address from [public].host, anything in ORCHESTRA_API_CORS_ORIGINS (comma-separated),
// and a request whose Origin IS the address it was sent to. That last case is SAME-ORIGIN
// — the page and the API on one address — which CORS never governed in the first place;
// treating it as cross-origin is what made a healthy dashboard log "blocked origin" on
// every request (2026-09-29). Requests with no Origin (curl, server-side callers) are
// allowed: CORS is a browser control and refusing them adds nothing.
// The decision itself lives in lib/cors-origin.ts, with its own tests.
app.use((req, res, next) => cors({
  origin(origin, cb) {
    // x-forwarded-host is trusted ONLY from a loopback peer — i.e. the dashboard proxy on
    // this machine, which is the only thing that should ever be forwarding for us. Any
    // client can send that header, and honouring it from an arbitrary peer would let a
    // caller nominate itself as same-origin and be handed Access-Control-Allow-Origin
    // (flagged in review, 2026-09-29). The API binds loopback today, so this costs nothing
    // and stops the bypass if it is ever exposed directly.
    const peer = req.socket.remoteAddress || '';
    const peerIsLocal = peer === '127.0.0.1' || peer === '::1' || peer === '::ffff:127.0.0.1';
    const decision = originDecision({
      origin: origin || undefined,
      forwardedHost: peerIsLocal ? String(req.headers['x-forwarded-host'] || '') || undefined : undefined,
      hostHeader: req.headers.host,
      allowlist: corsAllowlist(),
    });
    if (!decision.allow && origin && !warnedOrigins.has(origin)) {
      warnedOrigins.add(origin);
      // Say what actually happened: the header is withheld, so a CROSS-origin browser read
      // fails. Nothing else is refused, and same-origin traffic is untouched.
      console.warn(
        `[cors] withholding Access-Control-Allow-Origin for ${origin} — not the address ` +
        `this request was sent to, and not in the allowlist (${corsAllowlist().join(', ')}). ` +
        `Cross-origin browser reads from it will fail; set [public].host or ` +
        `ORCHESTRA_API_CORS_ORIGINS if it should be allowed.`,
      );
    }
    // Withhold the header rather than throwing: an Error here surfaces as a 500 that
    // pollutes error monitoring and masks real faults.
    return cb(null, decision.allow);
  },
  credentials: true,
})(req, res, next));
// Forensic access log.
// Forensic access log. Mounted before the routes and before the body parser so a
// request is recorded even if parsing rejects it.
app.use(accessLog);
app.use(express.json({ limit: '10mb' }));

app.get('/health', (_, res) => res.json({ status: 'ok', service: 'orchestraOS-api' }));
// /api/health — process up, DB open (a real SELECT through the native binding), for
// `orchestra doctor` and INSTALL's smoke check. 503 when the DB does not answer.
app.get('/api/health', (_, res) => {
  let dbOpen = false; let dbError: string | null = null;
  try { queryDb('SELECT 1 AS one'); dbOpen = true; } catch (e: any) { dbError = e?.message ?? String(e); }
  const body = { status: dbOpen ? 'ok' : 'degraded', service: 'orchestraOS-api', pid: process.pid,
    uptime_s: Math.round(process.uptime()), db: { open: dbOpen, error: dbError }, bindings: dbOpen };
  res.status(dbOpen ? 200 : 503).json(body);
});

// E4 — same-second agent-status SSE fan-out (read-only view of telemetryd's
// authoritative realtime/status.json; single-source-of-truth per DEC-1787826639).
app.get('/api/status/stream', (_req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });
  res.write('retry: 3000\n\n');
  addStatusSSEClient(res);
  const heartbeat = setInterval(() => {
    try { res.write(': ping\n\n'); } catch { /* client gone */ }
  }, 25000);
  heartbeat.unref?.();
  res.on('close', () => clearInterval(heartbeat));
});

// Caller identity. Derived by lib/principal, never inline from raw headers — the inline
// version defaulted to admin + wildcard scope and let `X-Orchestra-User: eve` mint an
// admin identity. Absent identity is a 401 here, never a default admin.
app.get('/api/me', (req, res) => {
  const p = principal(req);
  if (!p) { res.status(401).json({ error: 'unauthenticated' }); return; }
  res.json({
    username: p.username,
    role: p.role,
    allowed_agents: p.allowedAgents,
    client_scope: p.clientScope,
    trusted: p.trusted,
  });
});

// Optional features whose backing script does not ship in every install (lib/capabilities.ts).
// The dashboard hides an entry whose capability is false; its route answers 501. Booleans only.
app.get('/api/capabilities', (_req, res) => { res.json(capabilities()); });

// /new and /login-shell must mount BEFORE the agents router, whose '/:id/spawn'
// would otherwise swallow them as an agent id.
app.use('/api/agents', agentsNewRouter);
app.use('/api/agents', agentsRouter);
app.use('/api/agents', chatTranscriptRouter); // /:id/transcript (falls through from agentsRouter)
app.use('/api/agents', transcriptStreamRouter); // /:id/transcript/stream (F1 SSE lane; poll path above is the fallback)
app.use('/api/agents', agentSendRouter); // /:id/send (B1 send bridge, falls through from agentsRouter)
app.use('/api/tasks', tasksRouter);
app.use('/api/red-alert', redAlertRouter); // the report button = ticket gateway (docs/RED_ALERT.md)
app.use('/api/activity', activityRouter);
app.use('/api/system', systemRouter);
app.use('/api/memory', memoryRouter);
app.use('/api/roadmaps', roadmapsRouter);
app.use('/api/voice', voiceRouter);
app.use('/api/arturo', arturoRouter);
app.use('/api/approvals', approvalsRouter);
app.use('/api/analytics', analyticsRouter);
app.use('/api/workflows', workflowsRouter);
app.use('/api/skills', skillsRouter);
app.use('/api/transit', transitRouter);
app.use('/api/projects', projectsRouter);
app.use('/api/system/vps-auth', authRouter);
app.use('/api/uploads', uploadsRouter);
// G21: recover a hard-wrapped sign-in URL from a terminal pane so the UI can make it clickable.
app.use('/api/agents', paneUrlRouter);
app.use('/api/adaptive', adaptiveRouter);
app.use('/api/messages', messagesRouter);
app.use('/api/machines', machinesRouter);
app.use('/api/signing', signingRouter);
app.use('/api/approvals/unified', unifiedApprovalsRouter);
app.use('/api/learning', learningRouter);
app.use('/api/questionnaires', questionnairesRouter);
app.use('/api/auth', tokenAuthRouter);
app.use('/api/v2/tasks', tasksV2Router);
app.use('/api/north-stars', northStarsRouter);
app.use('/api/people', peopleRouter);
app.use('/api/agent-state', agentStateRouter);
app.use('/api/inspect-feedback', inspectFeedbackRouter);
app.use('/api/telemetry', telemetryRouter); // Build B: read-only telemetry query/stream (INERT — no cron/systemd)
app.use('/api/runtimes', runtimesAvailableRouter);
app.use('/api/facts', factsRouter);
app.use('/api/chat-history', chatHistoryRouter); // the /chat-history page's list + thread reads (router existed, was never mounted)

const ORCHESTRA_DIR_PATH = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
// Stored-XSS fix (attach spec §B.2.4, agent-state-truth): uploads are USER
// CONTENT served same-origin — html/htm/svg/xml would execute as this origin.
// Force active-content types to download; never let the browser sniff.
const FORCE_DOWNLOAD_EXTS = /\.(html?|svg|xml|xhtml)$/i;
app.use('/uploads', (req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  if (FORCE_DOWNLOAD_EXTS.test(req.path)) {
    res.setHeader('Content-Disposition', 'attachment');
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  }
  next();
}, express.static(join(ORCHESTRA_DIR_PATH, 'state', 'uploads')));

initActivityStream();
initStatusStream();

// PORT (supervisor) > ORCHESTRA_API_PORT (orchestra-env.sh) > [api] port in orchestra.toml.
const PORT = process.env.PORT || process.env.ORCHESTRA_API_PORT || loadConfig().apiPort;
const server = createServer(app);

// WebSocket terminal server on /ws/terminal. It is a shell into a seat's pane and browsers do
// not apply the same-origin policy to WebSockets, so every handshake passes lib/ws-origin.ts.
const wss = new WebSocketServer({ server, path: '/ws/terminal', verifyClient: (info: { req: IncomingMessage }) => {
  const req = info.req;
  const d = wsOriginDecision({
    origin: req.headers.origin,
    hostHeader: req.headers.host,
    forwardedHost: String(req.headers['x-forwarded-host'] || '') || undefined,
    peerAddress: req.socket.remoteAddress,
    port: Number(process.env.ORCHESTRA_DASHBOARD_PORT || loadConfig().dashboardPort),
    dashboardHost: process.env.ORCHESTRA_DASHBOARD_HOST || loadConfig().dashboardHost,
    extraHosts: process.env.ORCHESTRA_DASHBOARD_ALLOWED_HOSTS || '',
  });
  if (!d.allow) console.warn(`[ws] refused /ws/terminal from origin ${req.headers.origin} (${d.reason})`);
  return d.allow;
} });
setupTerminalWebSocket(wss);
setupVoiceLiveWebSocket(server);

// HOST must be passed explicitly: server.listen(PORT, cb) makes Node bind ALL interfaces,
// so `[api] host = "127.0.0.1"` in orchestra.toml was stated intent the code never honoured.
// Verified live before this change — lsof showed *:8888 and the LAN IP served /api/me.
const HOST = process.env.ORCHESTRA_API_HOST || loadConfig().apiHost || '127.0.0.1';

server.listen(Number(PORT), HOST, () => console.log(
  `OrchestraOS API on ${HOST}:${PORT} (WebSocket terminal enabled)`));

// A 60 s memory heartbeat so a supervisor restart leaves a trend in the API log
// (heap cap vs event-loop stall was unanswerable without it — docs/RED_ALERT.md api_health_fail).
setInterval(() => {
  const m = process.memoryUsage();
  const mb = (n: number) => Math.round(n / 1048576);
  console.log(`[mem ${new Date().toISOString()}] rss=${mb(m.rss)}MB heapUsed=${mb(m.heapUsed)}MB heapTotal=${mb(m.heapTotal)}MB ext=${mb(m.external)}MB up=${Math.round(process.uptime())}s`);
}, 60_000).unref();
