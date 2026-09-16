import { Router, Request, Response } from 'express';
import multer from 'multer';
import { join } from 'path';
import { existsSync, mkdirSync } from 'fs';

const router = Router();
const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const UPLOADS_DIR = join(ORCHESTRA_DIR, 'state', 'uploads');

if (!existsSync(UPLOADS_DIR)) mkdirSync(UPLOADS_DIR, { recursive: true });

// Hardened extension: lowercase, must be short alnum (SPEC_ios-attach §B.2.2 —
// closes the edge where a dot-less or slash-bearing name splices junk into the
// stored filename). Server generates the filename; client names are never paths.
function safeExt(originalname: string): string {
  const raw = (originalname.split('.').pop() || '').toLowerCase();
  return /^[a-z0-9]{1,8}$/.test(raw) ? raw : 'bin';
}

// Display-name sanitization (SPEC_ios-attach §B.3, REQUIRED): originalname is
// user-controlled text that will ride inside the [attached: "…"] marker of an
// injected message. Strip ALL control chars (a filename must never break out of
// its marker line), swap double quotes (grammar integrity), collapse
// whitespace, cap at 80 chars middle-ellipsized. Display-only — never a path.
export function sanitizeDisplayName(name: string): string {
  let s = String(name || '');
  // Multipart senders (URLSession, curl) may percent-encode the filename —
  // "Photo%20Aug9.jpg" reached prod 2026-08-09. Decode BEFORE sanitizing so
  // the display name is human again; decode failures keep the raw string.
  try { if (/%[0-9A-Fa-f]{2}/.test(s)) s = decodeURIComponent(s); } catch { /* keep raw */ }
  s = s
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x1f\x7f\u2028\u2029]/g, '')
    .replace(/"/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
  if (s.length > 80) s = s.slice(0, 38) + '…' + s.slice(-38);
  return s;
}

const storage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, UPLOADS_DIR),
  filename: (_req, file, cb) => {
    const ts = Math.floor(Date.now() / 1000);
    const hash = Math.random().toString(36).substring(2, 8);
    cb(null, `${ts}_${hash}.${safeExt(file.originalname)}`);
  },
});

// Filter by EXTENSION, not MIME type — srt/vtt/m4a/json upload with unreliable
// or empty browser MIME types, so an extension allow-list is the robust check.
const ALLOWED_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp',          // images
  'pdf', 'csv', 'txt', 'md', 'json', 'html', 'htm', 'vtt', 'srt', // docs/text/transcripts
  'docx', 'xlsx', 'pptx', 'zip',                // office/archive
  'm4a', 'mp3', 'wav',                          // audio
  'mp4', 'mov', 'm4v', 'webm',                  // video (the operator 2026-08-10: glitch
  // recordings to any agent, consumed as data by visual-qa-loop — never
  // inline-rendered or served executable; same posture as other uploads)
]);

const upload = multer({
  storage,
  limits: { fileSize: 100 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    const ext = (file.originalname.split('.').pop() || '').toLowerCase();
    cb(null, ALLOWED_EXTS.has(ext));
  },
});

router.post('/', upload.single('file'), (req: Request, res: Response) => {
  try {
    if (!req.file) {
      res.status(400).json({ error: `No valid file provided. Allowed extensions: ${[...ALLOWED_EXTS].join(', ')}` });
      return;
    }
    const absPath = join(UPLOADS_DIR, req.file.filename);
    const relUrl = `/uploads/${req.file.filename}`;
    res.json({
      path: absPath, url: relUrl, filename: req.file.filename,
      originalname: sanitizeDisplayName(req.file.originalname),   // spec §A1/§B.3
      size: req.file.size,
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
