import fs from 'fs';
import path from 'path';
import chokidar from 'chokidar';
import type { Response } from 'express';
import { loadConfig } from '../lib/config.js';

const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const ACTIVITY_FILE = path.join(ORCHESTRA_DIR, 'activity.jsonl');

const clients = new Set<Response>();
let lastSize = 0;
let watcher: ReturnType<typeof chokidar.watch> | null = null;

function ensureFile(): void {
  try {
    if (!fs.existsSync(ACTIVITY_FILE)) {
      fs.writeFileSync(ACTIVITY_FILE, '', 'utf-8');
    }
  } catch {
    // ignore — parent dir may not exist
  }
}

function broadcastNewLines(): void {
  try {
    const stat = fs.statSync(ACTIVITY_FILE);
    if (stat.size <= lastSize) {
      // File was truncated or unchanged
      if (stat.size < lastSize) lastSize = 0;
      return;
    }
    const fd = fs.openSync(ACTIVITY_FILE, 'r');
    const buf = Buffer.alloc(stat.size - lastSize);
    fs.readSync(fd, buf, 0, buf.length, lastSize);
    fs.closeSync(fd);
    lastSize = stat.size;

    const lines = buf.toString('utf-8').trim().split('\n').filter(Boolean);
    for (const line of lines) {
      try {
        const parsed = JSON.parse(line);
        const payload = `data: ${JSON.stringify(parsed)}\n\n`;
        for (const client of clients) {
          try {
            client.write(payload);
          } catch {
            clients.delete(client);
          }
        }
      } catch {
        // skip malformed lines
      }
    }
  } catch {
    // file read error — ignore
  }
}

export function initActivityStream(): void {
  ensureFile();
  try {
    const stat = fs.statSync(ACTIVITY_FILE);
    lastSize = stat.size;
  } catch {
    lastSize = 0;
  }

  watcher = chokidar.watch(ACTIVITY_FILE, {
    persistent: true,
    usePolling: false,
    ignoreInitial: true,
  });
  watcher.on('change', broadcastNewLines);
}

export function addSSEClient(res: Response): void {
  clients.add(res);
  res.on('close', () => {
    clients.delete(res);
  });
}

export function removeSSEClient(res: Response): void {
  clients.delete(res);
}

export function getSSEClientCount(): number {
  return clients.size;
}

export function stopActivityStream(): void {
  if (watcher) {
    watcher.close();
    watcher = null;
  }
}
