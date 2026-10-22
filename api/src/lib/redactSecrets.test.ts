/**
 * Every fake secret here is BUILT at run time (prefix + filler) so no secret-shaped literal sits in
 * the source for the secret scanners to flag. Run: npx tsx --test src/lib/redactSecrets.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { redactSecrets } from './redactSecrets.js';

const fill = (n: number, c = 'a') => c.repeat(n);
const SHAPES: [string, string][] = [
  ['Anthropic key', 'sk-ant-' + fill(40)],
  ['OpenAI key', 'sk-proj-' + fill(40)],
  ['Stripe key', 'sk_' + 'live_' + fill(24, 'b')],
  ['GitHub token', 'ghp_' + fill(36, 'c')],
  ['Slack token', 'xox' + 'b-' + fill(20, '1')],
  ['AWS key', 'AKIA' + fill(16, 'Q')],
  ['Google key', 'AIza' + fill(35, 'd')],
  ['Google OAuth secret', 'GOCSPX-' + fill(28, 'o')],
  ['Google OAuth token', 'ya29.' + fill(40, 'p')],
  ['JWT', 'eyJ' + fill(20, 'e') + '.' + fill(20, 'f') + '.' + fill(20, 'g')],
];

for (const [kind, secret] of SHAPES) {
  test(`${kind} is removed`, () => {
    const r = redactSecrets(`before ${secret} after`);
    assert.ok(!r.text.includes(secret), r.text);
    assert.equal(r.text, `before [redacted: ${kind}] after`);
    assert.equal(r.count, 1);
  });
}

test('a private key block goes whole', () => {
  const pem = ['-----BEGIN ' + 'OPENSSH PRIVATE KEY-----', fill(60, 'k'), fill(60, 'k'), '-----END ' + 'OPENSSH PRIVATE KEY-----'].join('\n');
  const r = redactSecrets(`key:\n${pem}\nend`);
  assert.equal(r.text, 'key:\n[redacted: private key]\nend');
});

test('Bearer keeps the word; a URL keeps its user and host; a query keeps its name', () => {
  assert.equal(redactSecrets('Authorization: Bearer ' + fill(32, 'z')).text, 'Authorization: Bearer [redacted: bearer token]');
  assert.equal(redactSecrets('postgres://app:' + 'hunter2pw' + '@db.local/x').text, 'postgres://app:[redacted: URL password]db.local/x');
  assert.equal(redactSecrets('https://x.test/cb?token=' + fill(12, 'y') + '&n=1').text, 'https://x.test/cb?token=[redacted: URL token]&n=1');
});

test('an assignment whose NAME says secret keeps the name, loses the value (gm)', () => {
  const v = fill(12, 'v');
  assert.equal(redactSecrets(`GITHUB_TOKEN=${v}`).text, 'GITHUB_TOKEN=[redacted: named secret]');
  assert.equal(redactSecrets(`"db_password": "${v}"`).text, '"db_password": "[redacted: named secret]"');
  assert.equal(redactSecrets(`OPENAI_API_KEY: ${v}`).text, 'OPENAI_API_KEY: [redacted: named secret]');
});

test('the server\'s own secrets by exact value', () => {
  const tok = fill(40, 'T');
  const r = redactSecrets(`curl -H x:${tok} and again ${tok}`, [tok]);
  assert.equal(r.text, 'curl -H x:[redacted: server secret] and again [redacted: server secret]');
  assert.equal(r.kinds['server secret'], 2);
});

test('shape only: hashes, uuids, commit shas and ordinary words stay', () => {
  const keep = [
    'commit 6a8dfa79d2c160539cf56a31c3a494a75c007834',
    'sha256 101a4258f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f',
    'id 00000000-1111-4222-8333-444444444444',
    'the task is done; key points below',
    'TOKEN_TTL=60',                                                   // the value is 2 chars: too short to be a secret
    'see docs/SECRET_SCAN.md',
  ];
  for (const s of keep) assert.equal(redactSecrets(s).text, s, s);
});
