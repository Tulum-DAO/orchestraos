import { describe, it, expect, vi } from 'vitest';
import { handleIssuePreview, handleIssueCreate } from './red-alert.js';

function mockRes() {
  const res: any = { statusCode: 200, body: null };
  res.status = (c: number) => { res.statusCode = c; return res; };
  res.json = (b: any) => { res.body = b; return res; };
  return res;
}

describe('red-alert issue endpoints', () => {
  it('preview returns parsed JSON from runner', async () => {
    const run = vi.fn(async () => ({
      code: 0,
      stdout: JSON.stringify({ ok: true, can_file: false, repo_configured: false, title: 't', body: 'b' }),
      stderr: '',
    }));
    const res = mockRes();
    await handleIssuePreview({ params: { id: 'ra_1' } } as any, res, run);
    expect(res.statusCode).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(run).toHaveBeenCalledWith(['preview', 'ra_1']);
  });

  it('create refuses without confirm:true', async () => {
    const run = vi.fn();
    const res = mockRes();
    await handleIssueCreate({ params: { id: 'ra_1' }, body: {} } as any, res, run);
    expect(res.statusCode).toBe(400);
    expect(res.body.error).toMatch(/confirm/);
    expect(run).not.toHaveBeenCalled();
  });

  it('create with confirm passes --yes', async () => {
    const run = vi.fn(async () => ({
      code: 0,
      stdout: JSON.stringify({ ok: true, action: 'create', dry_run: true }),
      stderr: '',
    }));
    const res = mockRes();
    await handleIssueCreate({ params: { id: 'ra_1' }, body: { confirm: true, dry_run: true } } as any, res, run);
    expect(res.statusCode).toBe(200);
    expect(run).toHaveBeenCalledWith(['create', 'ra_1', '--yes', '--dry-run']);
  });
});
