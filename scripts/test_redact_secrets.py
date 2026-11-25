"""Credential redaction: every shape is caught, ordinary identifiers survive, known values are
caught even when no shape knows them, and the CLI is a plain stdin -> stdout filter.

Fake tokens are assembled at run time ("gh" + "p_" + ...) so no token-shaped literal sits in this
file for a secret scanner to flag."""
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.redact_secrets as R

ROOT = Path(__file__).resolve().parent.parent
P = R.PLACEHOLDER


def _tok(*parts):
    return "".join(parts)


A36 = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"           # 36 mixed chars  # pragma: allowlist secret (fake test fixture)
B64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0123456789+/"  # base64 alphabet sample  # pragma: allowlist secret (fake test fixture)

# (kind, text containing one secret, the secret itself)
SHAPES = [
    ("anthropic", "key " + _tok("sk-", "ant-", "api03-", A36) + " end", _tok("sk-", "ant-", "api03-", A36)),
    ("openai", "OPENAI " + _tok("sk-", "proj-", A36) + " x", _tok("sk-", "proj-", A36)),
    ("github", "push with " + _tok("gh", "p_", A36), _tok("gh", "p_", A36)),
    ("github-pat", "pat " + _tok("github", "_pat_", "11ABCDEFG0", A36), _tok("github", "_pat_", "11ABCDEFG0", A36)),
    ("slack", "slack " + _tok("xo", "xb-", "1234567890-", "abcdefABCDEF12"), _tok("xo", "xb-", "1234567890-", "abcdefABCDEF12")),  # pragma: allowlist secret (fake test fixture)
    ("aws-key-id", "id=" + _tok("AK", "IA", "IOSFODNN7EXAMPLE") + ";", _tok("AK", "IA", "IOSFODNN7EXAMPLE")),
    ("aws-sts", "sts " + _tok("AS", "IA", "Z9Y8X7W6V5U4T3S2"), _tok("AS", "IA", "Z9Y8X7W6V5U4T3S2")),
    ("google", "maps " + _tok("AI", "za", "SyA-1234567890abcdefghijklmnopqrstu"), _tok("AI", "za", "SyA-1234567890abcdefghijklmnopqrstu")),  # pragma: allowlist secret (fake test fixture)
    ("gitlab", "gl " + _tok("gl", "pat-", "xY9zW8vU7tS6rQ5pO4nM"), _tok("gl", "pat-", "xY9zW8vU7tS6rQ5pO4nM")),
    ("telegram", "bot " + _tok("123456789", ":", "AAH", A36), _tok("123456789", ":", "AAH", A36)),
    ("jwt", "jwt " + _tok("ey", "JhbGciOiJIUzI1NiJ9", ".", "ey", "JzdWIiOiIxMjM0NTY3ODkwIn0", ".", "dozjgNryP4J3jVmNHl0w5N"),
     _tok("ey", "JhbGciOiJIUzI1NiJ9", ".", "ey", "JzdWIiOiIxMjM0NTY3ODkwIn0", ".", "dozjgNryP4J3jVmNHl0w5N")),
    ("pem", "x\n" + _tok("-----BEGIN ", "RSA PRIVATE KEY-----\nMIIEow", B64, "\n-----END ", "RSA PRIVATE KEY-----") + "\ny",
     _tok("-----BEGIN ", "RSA PRIVATE KEY-----\nMIIEow", B64, "\n-----END ", "RSA PRIVATE KEY-----")),
    ("bearer", "curl -H 'Authorization: Bearer abc123DEF456ghi789'", "abc123DEF456ghi789"),
    ("basic", "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw=="),
    ("api-key-header", 'curl -H "X-Api-Key: live_9f8e7d6c5b4a" https://x', "live_9f8e7d6c5b4a"),
    ("cookie-header", "Cookie: sid=s%3Aqrstuvwxyz9876; theme=dark", "sid=s%3Aqrstuvwxyz9876; theme=dark"),
    ("query-param", "GET https://api.example.com/v1?user=7&access_token=ya29abcdef12345&x=1", "ya29abcdef12345"),
    ("query-sig", "https://bucket.example.com/f.png?X-Expires=9&sig=Zm9vYmFyYmF6", "Zm9vYmFyYmF6"),
    ("url-password", "DATABASE_URL postgres://app:hunter2hunter2@db.internal:5432/x", "hunter2hunter2"),  # pragma: allowlist secret (fake test fixture)
    ("url-password-empty-user", "redis://:s3cr3tpass@cache:6379/0", "s3cr3tpass"),
    ("data-uri", "img src=data:image/png;base64," + B64 * 2 + " alt", B64 * 2),
    ("env-assign", "export STRIPE_SECRET_KEY=rk_live_51Habc0987zyx", "rk_live_51Habc0987zyx"),
    ("yaml-field", "password: correct-horse-battery", "correct-horse-battery"),
    ("json-field", '{"client_secret": "GOCSPX-abcdefgh1234", "n": 1}', "GOCSPX-abcdefgh1234"),  # pragma: allowlist secret (fake test fixture)
]


@pytest.mark.parametrize("kind,text,secret", SHAPES, ids=[s[0] for s in SHAPES])
def test_every_shape_is_redacted_and_the_secret_is_gone(kind, text, secret):
    out = R.redact(text)
    assert secret not in out, f"{kind}: secret survived: {out!r}"
    assert P in out


def test_the_readable_part_of_a_match_stays():
    assert R.redact("Authorization: Bearer abc123DEF456ghi789") == f"Authorization: Bearer {P}"
    assert R.redact("postgres://app:hunter2hunter2@db:5432/x") == f"postgres://app:{P}@db:5432/x"  # pragma: allowlist secret (fake test fixture)
    assert R.redact("export GITHUB_TOKEN=abcdefgh12345678") == f"export GITHUB_TOKEN={P}"
    assert R.redact("?page=2&api_key=abcd1234&q=x") == f"?page=2&api_key={P}&q=x"
    out = R.redact("data:image/png;base64," + B64 * 2)
    assert out == f"data:image/png;base64,{P}"


NEGATIVES = [
    "session 00000000-1111-4222-8333-444444444444 resumed",                      # UUID
    "session_id: 00000000-1111-4222-8333-444444444444",                          # session ids are not secrets
    "integrity sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU= ok",          # content hash
    "image hash 3q2+7w4bU0yH6pO5c1kGZx8a9VQe7mLrT2sN0dJfKpA=",                  # base64 image hash
    "max_tokens=4096 input_tokens: 123456789 token_count=88888888",              # counters, not secrets
    "author: Ada Lovelace wrote the task-runner documentation on risk-assessment",  # prose with sk- inside words
    "PWD=/home/dev/project cwd /tmp",                                       # paths
    "the url https://example.com/path?page=3&sort=asc stays",                    # harmless query
    "see AKIAPAGE in the docs; AIza is a prefix; ghp_ alone",                    # bare prefixes, too short
    'BEARER_TOKEN = secrets.get("CUSTOM_LLM_BEARER", "")',                      # code that READS a secret
    "    api_key=GEMINI_API_KEY,",                                              # an env var's name
    "TOKEN=$GITHUB_TOKEN_VALUE password: ${{ secrets.DEPLOY_PASSWORD }}",        # shell / CI references
    "a trailing note, then commit 9fceb02d0ae598e95dc970b74767f19372d61af8 on main",  # git SHA
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_ordinary_identifiers_survive_unchanged(text):
    assert R.redact(text) == text


def test_the_known_value_pass_catches_a_value_no_shape_knows():
    secret = "plainword-zebra-42-orchid"          # no prefix, no credential key next to it  # pragma: allowlist secret (fake test fixture)
    text = f"deploy log: connected with {secret} at 10:02"
    assert R.redact(text) == text, "precondition: no shape matches this value"
    assert R.redact(text, known_values=[secret]) == f"deploy log: connected with {P} at 10:02"
    assert R.redact(text + " and again " + secret, known_values=[secret]).count(P) == 2


def test_short_known_values_are_ignored_so_common_words_survive():
    assert R.redact("the cat sat", known_values=["cat", "the"]) == "the cat sat"


def test_known_values_from_env_take_credential_names_only_and_skip_paths():
    env = {"MY_API_KEY": "value-from-env-123", "GOOGLE_APPLICATION_CREDENTIALS": "/srv/key.json",  # pragma: allowlist secret (fake test fixture)
           "PWD": "/home/dev", "HOME": "/home/dev", "MAX_TOKENS": "4096", "DB_PASSWORD": "pw-0987654321"}  # pragma: allowlist secret (fake test fixture)
    assert sorted(R.known_values_from_env(env)) == ["pw-0987654321", "value-from-env-123"]


def test_spans_are_sorted_and_overlaps_merge():
    text = "Authorization: Bearer abc123DEF456ghi789 and " + _tok("gh", "p_", A36)
    spans = R.Redactor().spans(text)
    assert [text[s:e] for s, e, _ in spans] == ["abc123DEF456ghi789", _tok("gh", "p_", A36)]
    assert spans == sorted(spans)


def test_an_unterminated_private_key_is_redacted_to_the_end():
    text = "head\n" + _tok("-----BEGIN ", "OPENSSH PRIVATE KEY-----\n", B64, "\n", B64)
    assert R.redact(text) == "head\n" + P


def test_redaction_is_idempotent():
    once = R.redact(" ".join(s[1] for s in SHAPES))
    assert R.redact(once) == once


def test_cli_filters_stdin_with_a_known_value_file(tmp_path):
    kf = tmp_path / "known.txt"
    kf.write_text("plainword-zebra-42-orchid\n")
    src = "a " + _tok("gh", "p_", A36) + " b plainword-zebra-42-orchid c\n"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "redact_secrets.py"), "--known-file", str(kf)],
                       input=src, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"a {P} b {P} c\n"
