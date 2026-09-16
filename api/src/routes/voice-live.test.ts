/**
 * B3 voice bridge proxy test — a fake upstream `/live` server stands in for
 * watch_gateway.py:9091. No real mic/gateway/network touched.
 * Run: npx tsx --test src/routes/voice-live.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'http';
import { createHmac } from 'crypto';
import { writeFileSync, mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { WebSocket, WebSocketServer } from 'ws';
import { setupVoiceLiveWebSocket, VOICE_LIVE_PATH, isAllowedOrigin } from './voice-live.js';

const SESSION_SECRET = 'orchestraOS-session-2026';

function sessionCookie(): string {
  const payload = JSON.stringify({ u: 'operator', r: 'admin', c: '', exp: Date.now() + 3600_000 });
  const b64 = Buffer.from(payload).toString('base64url');
  const sig = createHmac('sha256', SESSION_SECRET).update(b64).digest('base64url');
  return `orchestra_session=${b64}.${sig}`;
}

async function withFakeGateway<T>(
  handler: (ws: WebSocket, req: any) => void,
  fn: (gatewayPort: number) => Promise<T>,
): Promise<T> {
  const gw = new WebSocketServer({ port: 0, path: '/live' });
  await new Promise<void>((resolve) => gw.once('listening', () => resolve()));
  gw.on('connection', handler);
  const port = (gw.address() as any).port;
  try {
    return await fn(port);
  } finally {
    gw.close();
  }
}

async function withTokenFile<T>(fn: (path: string) => Promise<T>): Promise<T> {
  const dir = mkdtempSync(join(tmpdir(), 'voice-live-test-'));
  const path = join(dir, 'watch-gateway-token');
  writeFileSync(path, 'test-bearer-token-123\n');
  try {
    return await fn(path);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

async function startProxyServer(gatewayWsUrl: string) {
  const server = createServer((_req, res) => { res.writeHead(404); res.end(); });
  setupVoiceLiveWebSocket(server, gatewayWsUrl);
  await new Promise<void>((resolve) => server.listen(0, resolve));
  const port = (server.address() as any).port;
  return { server, port };
}

test('rejects a WS upgrade with no session cookie (401, socket destroyed)', async () => {
  await withTokenFile(async (tokenPath) => {
    process.env.WATCH_GATEWAY_TOKEN_FILE = tokenPath;
    await withFakeGateway(() => {}, async (gwPort) => {
      const { server, port } = await startProxyServer(`ws://127.0.0.1:${gwPort}`);
      try {
        await new Promise<void>((resolve, reject) => {
          // Same-host Origin so this exercises the AUTH check (401), not the
          // Origin check (403) — that's covered by its own tests below.
          const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}`, {
            headers: { origin: `http://127.0.0.1:${port}` },
          });
          ws.on('open', () => reject(new Error('should not have opened without auth')));
          ws.on('unexpected-response', (_r, res) => {
            assert.equal(res.statusCode, 401);
            resolve();
          });
          ws.on('error', () => { /* expected on 401 in some environments */ resolve(); });
        });
      } finally {
        server.close();
        delete process.env.WATCH_GATEWAY_TOKEN_FILE;
      }
    });
  });
});

test('LOUD-closes when the gateway bearer token file is missing (W1, no silent hang)', async () => {
  process.env.WATCH_GATEWAY_TOKEN_FILE = '/nonexistent/path/for/test/watch-gateway-token';
  await withFakeGateway(() => {}, async (gwPort) => {
    const { server, port } = await startProxyServer(`ws://127.0.0.1:${gwPort}`);
    try {
      await new Promise<void>((resolve, reject) => {
        const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}`, {
          headers: { cookie: sessionCookie(), origin: `http://127.0.0.1:${port}` },
        });
        ws.on('open', () => reject(new Error('should not open when gateway token is missing')));
        ws.on('unexpected-response', (_r, res) => {
          assert.equal(res.statusCode, 502);
          resolve();
        });
        ws.on('error', () => resolve());
      });
    } finally {
      server.close();
      delete process.env.WATCH_GATEWAY_TOKEN_FILE;
    }
  });
});

test('authed browser <-> proxy <-> fake gateway pipes bytes both ways', async () => {
  await withTokenFile(async (tokenPath) => {
    process.env.WATCH_GATEWAY_TOKEN_FILE = tokenPath;
    const received: Array<{ isBinary: boolean; data: string }> = [];
    await withFakeGateway((gwWs, req) => {
      // MEDIUM finding fix: bearer travels as a header, never in the URL.
      const url = new URL(req.url, 'http://internal');
      assert.equal(url.searchParams.get('token'), null, 'token must never appear in the upstream URL');
      assert.equal(url.searchParams.get('voice'), 'Charon');
      assert.equal(req.headers['authorization'], 'Bearer test-bearer-token-123');
      gwWs.on('message', (data, isBinary) => {
        received.push({ isBinary, data: isBinary ? '<binary>' : data.toString() });
        if (!isBinary) gwWs.send(JSON.stringify({ event: 'connected', session_id: 'vc_live_test1234' }));
      });
      gwWs.send(Buffer.from([1, 2, 3, 4])); // simulate a 24kHz PCM frame down
    }, async (gwPort) => {
      const { server, port } = await startProxyServer(`ws://127.0.0.1:${gwPort}`);
      try {
        await new Promise<void>((resolve, reject) => {
          const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}?voice=Charon`, {
            headers: { cookie: sessionCookie(), origin: `http://127.0.0.1:${port}` },
          });
          const seen: any[] = [];
          ws.on('open', () => {
            ws.send(JSON.stringify({ route: '/agent', focusedEntity: null }));
            ws.send(Buffer.from([9, 9, 9]), { binary: true });
          });
          ws.on('message', (data, isBinary) => {
            seen.push(isBinary ? '<binary>' : JSON.parse(data.toString()));
            if (seen.length === 2) {
              assert.deepEqual(seen[0], '<binary>');
              assert.deepEqual(seen[1], { event: 'connected', session_id: 'vc_live_test1234' });
              ws.close();
            }
          });
          ws.on('close', () => {
            try {
              assert.equal(received.length, 2);
              assert.equal(received[0].isBinary, false);
              assert.deepEqual(JSON.parse(received[0].data), { route: '/agent', focusedEntity: null });
              assert.equal(received[1].isBinary, true);
              resolve();
            } catch (e) { reject(e); }
          });
          ws.on('error', reject);
        });
      } finally {
        server.close();
        delete process.env.WATCH_GATEWAY_TOKEN_FILE;
      }
    });
  });
});

// ── CSWSH guard (security review HIGH finding, commit 4840bf6247) ──────────

test('isAllowedOrigin: same-host origin is allowed', () => {
  assert.equal(isAllowedOrigin('http://127.0.0.1:8888', '127.0.0.1:8888'), true);
});

test('isAllowedOrigin: missing Origin is rejected', () => {
  assert.equal(isAllowedOrigin(undefined, '127.0.0.1:8888'), false);
});

test('isAllowedOrigin: cross-site origin with no allowlist match is rejected', () => {
  assert.equal(isAllowedOrigin('https://evil.example', '127.0.0.1:8888'), false);
});

test('isAllowedOrigin: env allowlist accepts a listed origin/host', () => {
  process.env.VOICE_LIVE_ALLOWED_ORIGINS = 'https://ok.example, other.example';
  try {
    assert.equal(isAllowedOrigin('https://ok.example', '127.0.0.1:8888'), true);
    assert.equal(isAllowedOrigin('https://other.example', '127.0.0.1:8888'), true);
    assert.equal(isAllowedOrigin('https://not-listed.example', '127.0.0.1:8888'), false);
  } finally {
    delete process.env.VOICE_LIVE_ALLOWED_ORIGINS;
  }
});

test('cross-origin upgrade with a VALID cookie is still 403 (no auth oracle)', async () => {
  await withTokenFile(async (tokenPath) => {
    process.env.WATCH_GATEWAY_TOKEN_FILE = tokenPath;
    let gatewayContacted = false;
    await withFakeGateway(() => { gatewayContacted = true; }, async (gwPort) => {
      const { server, port } = await startProxyServer(`ws://127.0.0.1:${gwPort}`);
      try {
        await new Promise<void>((resolve, reject) => {
          const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}`, {
            headers: { cookie: sessionCookie(), origin: 'https://evil.example' },
          });
          ws.on('open', () => reject(new Error('cross-origin upgrade must never open')));
          ws.on('unexpected-response', (_r, res) => {
            assert.equal(res.statusCode, 403, 'a valid cookie must NOT turn a cross-origin request into 401 — it must be 403 before auth ever runs');
            resolve();
          });
          ws.on('error', () => resolve());
        });
        // The Origin check must reject before we'd ever dial the gateway.
        await new Promise((r) => setTimeout(r, 50));
        assert.equal(gatewayContacted, false, 'browserIsAuthenticated/upstream path must never run for a rejected Origin');
      } finally {
        server.close();
        delete process.env.WATCH_GATEWAY_TOKEN_FILE;
      }
    });
  });
});

test('missing Origin header is 403 even with a valid cookie', async () => {
  await withTokenFile(async (tokenPath) => {
    process.env.WATCH_GATEWAY_TOKEN_FILE = tokenPath;
    await withFakeGateway(() => {}, async (gwPort) => {
      const { server, port } = await startProxyServer(`ws://127.0.0.1:${gwPort}`);
      try {
        await new Promise<void>((resolve, reject) => {
          // node's `ws` client does not send an Origin header by default —
          // exactly the "absent Origin" case the spec calls out.
          const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}`, {
            headers: { cookie: sessionCookie() },
          });
          ws.on('open', () => reject(new Error('should not open with no Origin')));
          ws.on('unexpected-response', (_r, res) => {
            assert.equal(res.statusCode, 403);
            resolve();
          });
          ws.on('error', () => resolve());
        });
      } finally {
        server.close();
        delete process.env.WATCH_GATEWAY_TOKEN_FILE;
      }
    });
  });
});

test('same-host origin proceeds past the CSWSH guard (reaches the auth check)', async () => {
  await withTokenFile(async (tokenPath) => {
    process.env.WATCH_GATEWAY_TOKEN_FILE = tokenPath;
    await withFakeGateway(() => {}, async (gwPort) => {
      const { server, port } = await startProxyServer(`ws://127.0.0.1:${gwPort}`);
      try {
        await new Promise<void>((resolve, reject) => {
          // Same host, no cookie -> must get 401 (auth), NOT 403 (origin).
          const ws = new WebSocket(`ws://127.0.0.1:${port}${VOICE_LIVE_PATH}`, {
            headers: { origin: `http://127.0.0.1:${port}` },
          });
          ws.on('open', () => reject(new Error('should not open without a cookie')));
          ws.on('unexpected-response', (_r, res) => {
            assert.equal(res.statusCode, 401, 'same-host origin must fall through to the auth check, not 403');
            resolve();
          });
          ws.on('error', () => resolve());
        });
      } finally {
        server.close();
        delete process.env.WATCH_GATEWAY_TOKEN_FILE;
      }
    });
  });
});
