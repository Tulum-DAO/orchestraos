/**
 * RED-first for attachments on the Arturo composer.
 *
 * The operator, 2026-09-18: "I still cannot upload documents to this arturo agent by
 * clicking the plus button. That's its whole point."
 *
 * POST /api/uploads already exists and works (multer, 100MB cap, extension allowlist) — the
 * + button was simply never wired to it. These pin the client half: what the upload returns,
 * what the turn carries so Arturo can actually READ the file, and what happens when the
 * upload is refused.
 */
import assert from 'node:assert';
import { uploadAttachment, attachmentPreamble, describeAttachment } from './arturoUpload.ts';

// --- upload: talks to the existing endpoint, returns what the turn needs ---------------------
{
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = { url, method: init.method, isForm: init.body instanceof FormData };
    return { ok: true, status: 200, json: async () => ({
      path: '/data/state/uploads/abc.pdf', url: '/uploads/abc.pdf',
      filename: 'abc.pdf', originalname: 'Q3 plan.pdf', size: 1234 }) };
  };
  const file = new File([new Uint8Array([1, 2, 3])], 'Q3 plan.pdf', { type: 'application/pdf' });
  const got = await uploadAttachment(file);
  assert.equal(seen.url, '/api/uploads');
  assert.equal(seen.method, 'POST');
  assert.equal(seen.isForm, true);                 // multipart, the field the route expects
  assert.equal(got.ok, true);
  assert.equal(got.path, '/data/state/uploads/abc.pdf');
  assert.equal(got.name, 'Q3 plan.pdf');
  assert.equal(got.size, 1234);
}

// --- a refused file is reported, never silently dropped ---------------------------------------
{
  globalThis.fetch = async () => ({ ok: false, status: 400,
    json: async () => ({ error: 'No valid file provided. Allowed extensions: pdf, txt' }) });
  const got = await uploadAttachment(new File(['x'], 'virus.exe'));
  assert.equal(got.ok, false);
  assert.match(got.error, /allowed extensions/i);
}
{
  globalThis.fetch = async () => { throw new Error('network'); };
  const got = await uploadAttachment(new File(['x'], 'a.txt'));
  assert.equal(got.ok, false);
  assert.ok(got.error);
}

// --- the turn must tell Arturo where the file IS, or the upload is decoration ------------------
{
  // Arturo's brain runs on this machine with tool access, so an absolute PATH is what makes
  // the document actually readable. A name alone would look like it worked and do nothing.
  const pre = attachmentPreamble([
    { name: 'Q3 plan.pdf', path: '/data/state/uploads/abc.pdf', size: 1234 },
  ]);
  assert.match(pre, /\/data\/state\/uploads\/abc\.pdf/);
  assert.match(pre, /Q3 plan\.pdf/);
  assert.match(pre, /read/i);                      // tells the brain it may open it
}
{
  const pre = attachmentPreamble([
    { name: 'a.txt', path: '/u/a.txt', size: 1 },
    { name: 'b.csv', path: '/u/b.csv', size: 2 },
  ]);
  assert.match(pre, /\/u\/a\.txt/);
  assert.match(pre, /\/u\/b\.csv/);
}
{
  assert.equal(attachmentPreamble([]), '');        // no attachments, no preamble
  assert.equal(attachmentPreamble(null), '');
}

// --- the chip the operator sees ----------------------------------------------------------------
{
  assert.match(describeAttachment({ name: 'Q3 plan.pdf', size: 2048 }), /Q3 plan\.pdf/);
  assert.match(describeAttachment({ name: 'x.pdf', size: 2048 }), /2 KB/);
  assert.match(describeAttachment({ name: 'x.pdf', size: 5 * 1024 * 1024 }), /5(\.0)? MB/);
}

console.log('arturoUpload.test.mjs: all assertions passed');
