import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { createServer } from 'http';
import { join } from 'path';
import { WebSocketServer } from 'ws';

import agentsRouter from './routes/agents.js';
import chatTranscriptRouter from './routes/chat-transcript.js';
import transcriptStreamRouter from './routes/transcript-stream.js';
import tasksRouter from './routes/tasks.js';
import activityRouter from './routes/activity.js';
import systemRouter from './routes/system.js';
import memoryRouter from './routes/memory.js';
import roadmapsRouter from './routes/roadmaps.js';
import voiceRouter from './routes/voice.js';
import approvalsRouter from './routes/approvals.js';
import analyticsRouter from './routes/analytics.js';
import workflowsRouter from './routes/workflows.js';
import experimentsRouter from './routes/experiments.js';
import skillsRouter from './routes/skills.js';
import transitRouter from './routes/transit.js';
import clientsRouter from './routes/clients.js';
import projectsRouter from './routes/projects.js';
import authRouter from './routes/auth.js';
import uploadsRouter from './routes/uploads.js';
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
import projectStatusRouter from './routes/project-status.js';
import telemetryRouter from './routes/telemetry.js';
import agentSendRouter from './routes/agent-send.js';
import runtimesAvailableRouter from './routes/runtimes-available.js';
import factsRouter from './routes/facts.js';
import { initActivityStream } from './services/activity-stream.js';
import { initStatusStream, addStatusSSEClient } from './services/status-stream.js';
import { setupTerminalWebSocket } from './routes/terminal.js';
import { setupVoiceLiveWebSocket } from './routes/voice-live.js';
import { loadConfig } from './lib/config.js';

const app = express();
app.use(cors());
app.use(express.json({ limit: '10mb' }));

app.get('/health', (_, res) => res.json({ status: 'ok', service: 'orchestraOS-api' }));

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

// User identity from combo-proxy auth headers
app.get('/api/me', (req, res) => {
  const username = req.headers['x-orchestra-user'] as string || loadConfig().operatorId;
  const role = req.headers['x-orchestra-role'] as string || 'admin';
  const clientScope = req.headers['x-orchestra-client'] as string || '';
  const allowedRaw = req.headers['x-orchestra-allowed-agents'] as string || '*';
  let allowed_agents: string | string[] = '*';
  if (clientScope) {
    allowed_agents = clientScope; // tag-based: API filters by client tag
  } else if (allowedRaw !== '*') {
    try { allowed_agents = JSON.parse(allowedRaw); } catch { allowed_agents = allowedRaw.split(',').map(s => s.trim()); }
  }
  res.json({ username, role, allowed_agents, client_scope: clientScope || null });
});

app.use('/api/agents', agentsRouter);
app.use('/api/agents', chatTranscriptRouter); // /:id/transcript (falls through from agentsRouter)
app.use('/api/agents', transcriptStreamRouter); // /:id/transcript/stream (F1 SSE lane; poll path above is the fallback)
app.use('/api/agents', agentSendRouter); // /:id/send (B1 send bridge, falls through from agentsRouter)
app.use('/api/tasks', tasksRouter);
app.use('/api/activity', activityRouter);
app.use('/api/system', systemRouter);
app.use('/api/memory', memoryRouter);
app.use('/api/roadmaps', roadmapsRouter);
app.use('/api/voice', voiceRouter);
app.use('/api/approvals', approvalsRouter);
app.use('/api/analytics', analyticsRouter);
app.use('/api/workflows', workflowsRouter);
app.use('/api/experiments', experimentsRouter);
app.use('/api/skills', skillsRouter);
app.use('/api/transit', transitRouter);
app.use('/api/clients', clientsRouter);
app.use('/api/projects', projectsRouter);
app.use('/api/system/vps-auth', authRouter);
app.use('/api/uploads', uploadsRouter);
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
app.use('/api/project-status', projectStatusRouter);
app.use('/api/telemetry', telemetryRouter); // Build B: read-only telemetry query/stream (INERT — no cron/systemd)
app.use('/api/runtimes', runtimesAvailableRouter);
app.use('/api/facts', factsRouter);

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

// WebSocket terminal server on /ws/terminal
const wss = new WebSocketServer({ server, path: '/ws/terminal' });
setupTerminalWebSocket(wss);
setupVoiceLiveWebSocket(server);

server.listen(PORT, () => console.log(`OrchestraOS API on :${PORT} (WebSocket terminal enabled)`));
