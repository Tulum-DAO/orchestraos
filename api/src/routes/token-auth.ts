import { Router, type Request, type Response } from 'express';
import { createHmac } from 'crypto';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const TENANTS_FILE = join(ORCHESTRA, 'state', 'tenants.json');
const JWT_SECRET = process.env.JWT_SECRET || 'orchestraOS-jwt-secret-2026';
const TOKEN_EXPIRY = 7 * 24 * 60 * 60;

function base64urlObj(obj: object): string {
  return Buffer.from(JSON.stringify(obj)).toString('base64url');
}

function signJwt(payload: object): string {
  const header = base64urlObj({ alg: 'HS256', typ: 'JWT' });
  const body = base64urlObj(payload);
  const sig = createHmac('sha256', JWT_SECRET).update(`${header}.${body}`).digest('base64url');
  return `${header}.${body}.${sig}`;
}

function verifyJwt(token: string): any | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const [header, body, sig] = parts;
    const expected = createHmac('sha256', JWT_SECRET).update(`${header}.${body}`).digest('base64url');
    if (sig !== expected) return null;
    const payload = JSON.parse(Buffer.from(body, 'base64url').toString());
    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch { return null; }
}

function loadTenants(): Record<string, any> {
  try {
    if (!existsSync(TENANTS_FILE)) return {};
    return JSON.parse(readFileSync(TENANTS_FILE, 'utf-8'));
  } catch { return {}; }
}

router.post('/login', (req: Request, res: Response) => {
  const { username, password } = req.body;
  if (!username || !password) { res.status(400).json({ error: 'username and password required' }); return; }
  const tenants = loadTenants();
  const tenant = tenants[username];
  if (!tenant || tenant.password !== password) { res.status(401).json({ error: 'Invalid credentials' }); return; }
  const now = Math.floor(Date.now() / 1000);
  const payload = { sub: username, role: tenant.role, client: tenant.client || null, name: tenant.name || username, iat: now, exp: now + TOKEN_EXPIRY };
  const token = signJwt(payload);
  res.setHeader('Set-Cookie', `orchestra_token=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${TOKEN_EXPIRY}`);
  res.json({ token, user: { username, role: tenant.role, client: tenant.client || null, name: tenant.name || username } });
});

router.post('/logout', (_req: Request, res: Response) => {
  res.setHeader('Set-Cookie', 'orchestra_token=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0');
  res.json({ logged_out: true });
});

router.get('/verify', (req: Request, res: Response) => {
  let token: string | null = null;
  const auth = req.headers.authorization;
  if (auth?.startsWith('Bearer ')) token = auth.slice(7);
  if (!token) { const c = req.headers.cookie || ''; const m = c.match(/orchestra_token=([^;]+)/); token = m ? m[1] : null; }
  if (!token) { res.status(401).json({ valid: false }); return; }
  const payload = verifyJwt(token);
  if (!payload) { res.status(401).json({ valid: false }); return; }
  res.json({ valid: true, user: { username: payload.sub, role: payload.role, client: payload.client, name: payload.name } });
});

export default router;
