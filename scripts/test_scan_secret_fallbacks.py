"""Tests for the secret-fallback gate.

Every secret value here is synthetic. The real literal this gate was written for is burned
(public history) and is deliberately NOT reproduced in this repo, not even as a test input.
"""
import subprocess
import sys
from pathlib import Path

SCAN = str(Path(__file__).parent / "scan_secret_fallbacks.py")


def _run(files, extra=()):
    return subprocess.run([sys.executable, SCAN, *extra, *map(str, files)],
                          capture_output=True, text=True)


def _write(tmp_path, name, body):
    f = tmp_path / name
    f.write_text(body)
    return f


# ---------------------------------------------------------------- the real shape

def test_the_shape_that_shipped_a_public_key_is_caught(tmp_path):
    """The regression. `SECRET = process.env.X || '<label>'` in a public repo IS a published key.

    The value that shipped read like a label, not a credential: low entropy, no token prefix,
    nothing detect-secrets scores. The gate must key on SHAPE, never on the value.
    """
    f = _write(tmp_path, "a.ts",
               "const JWT_SECRET = process.env.JWT_SECRET || 'projectName-jwt-label-2026';\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    r = _run([f])
    assert r.returncode == 1
    assert "JWT_SECRET" in r.stderr


def test_the_value_is_never_printed(tmp_path):
    """A gate that echoes the secret into CI logs has moved the leak, not closed it."""
    secret = "projectName-session-label-2026"  # pragma: allowlist secret
    f = _write(tmp_path, "a.ts", f"const SESSION_SECRET = process.env.SESSION_SECRET || '{secret}';\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    r = _run([f])
    assert r.returncode == 1
    assert secret not in r.stdout and secret not in r.stderr


def test_nullish_coalescing_is_the_same_bug(tmp_path):
    f = _write(tmp_path, "a.ts", "const API_KEY = process.env.API_KEY ?? 'some-default-key-value';\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    assert _run([f]).returncode == 1


def test_bracket_env_access_is_caught(tmp_path):
    f = _write(tmp_path, "a.ts",
               "const HMAC_SECRET = process.env['HMAC_SECRET'] || 'fallback-signing-value';\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    assert _run([f]).returncode == 1


def test_python_getenv_default_is_caught(tmp_path):
    f = _write(tmp_path, "a.py", "SESSION_SECRET = os.environ.get('SESSION_SECRET', 'a-default-secret')\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    assert _run([f]).returncode == 1


def test_python_or_literal_is_caught(tmp_path):
    f = _write(tmp_path, "a.py", "JWT_SECRET = os.getenv('JWT_SECRET') or 'another-default-secret'\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    assert _run([f]).returncode == 1


# ---------------------------------------------------------------- not the bug

def test_a_location_shaped_name_is_configuration_not_a_secret(tmp_path):
    """`*_FILE`/`*_URL`/`*_PATH` hold a LOCATION. A literal default for one is ordinary config,
    and flagging it would train people to add pragmas until the gate means nothing."""
    body = (
        "const JWT_SECRET_FILE = process.env.JWT_SECRET_FILE || '/etc/app/jwt.secret';\n"
        "const TOKEN_URL = process.env.TOKEN_URL || 'https://example.test/token';\n"
        "const SECRET_DIR = process.env.SECRET_DIR || '/var/lib/app/secrets';\n"
        "const API_KEY_HEADER = process.env.API_KEY_HEADER || 'x-api-key-header';\n"
    )
    f = _write(tmp_path, "a.ts", body)
    r = _run([f])
    assert r.returncode == 0, r.stderr


def test_a_non_literal_fallback_is_fine(tmp_path):
    body = (
        "const JWT_SECRET = process.env.JWT_SECRET || process.env.LEGACY_JWT_SECRET;\n"
        "const S2 = process.env.SECRET_TWO || readFileSync(p, 'utf-8');\n"
        "const S3 = process.env.SECRET_THREE || randomBytes(32).toString('hex');\n"
    )
    f = _write(tmp_path, "a.ts", body)
    r = _run([f])
    assert r.returncode == 0, r.stderr


def test_an_empty_or_short_fallback_is_not_a_key(tmp_path):
    """`|| ''` is fail-closed, not a published key. A very short literal cannot be a real secret
    and is almost always a sentinel, so the gate would only cry wolf."""
    body = (
        "const JWT_SECRET = process.env.JWT_SECRET || '';\n"
        "const OTHER_SECRET = process.env.OTHER_SECRET || 'none';\n"
    )
    f = _write(tmp_path, "a.ts", body)
    r = _run([f])
    assert r.returncode == 0, r.stderr


def test_a_name_without_a_secret_stem_is_ignored(tmp_path):
    f = _write(tmp_path, "a.ts", "const GREETING = process.env.GREETING || 'hello there friend';\n")
    assert _run([f]).returncode == 0


# ---------------------------------------------------------------- gate mechanics

def test_a_pragma_exempts_and_is_counted(tmp_path):
    f = _write(tmp_path, "a.ts",
               "const JWT_SECRET = process.env.JWT_SECRET || 'documented-dev-only-value';"  # secret-fallback-ok: synthetic fixture, this gate's own test input
               "  // secret-fallback-ok: dev fixture, never deployed\n")
    r = _run([f])
    assert r.returncode == 0, r.stderr
    assert "1 lines exempted by pragma" in r.stdout


def test_min_files_makes_coverage_enforced_not_printed(tmp_path):
    """A scan that silently matched zero files would otherwise pass, and nobody reads logs
    on a green run."""
    f = _write(tmp_path, "a.ts", "const x = 1;\n")
    r = _run([f], extra=("--min-files", "50"))
    assert r.returncode == 2
    assert "REFUSING" in r.stderr


def test_unscanned_extensions_do_not_inflate_the_count(tmp_path):
    f = _write(tmp_path, "a.md", "const JWT_SECRET = process.env.JWT_SECRET || 'in-prose-not-code';\n")  # secret-fallback-ok: synthetic fixture, this gate's own test input
    r = _run([f])
    assert r.returncode == 0
    assert "scanned 0 files" in r.stdout


def test_the_repo_itself_is_clean_under_this_gate():
    """The gate must pass on the tree it ships with, or CI is red on arrival -- and it must
    scan ITSELF, which only happens once it is tracked."""
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
    files = [f for f in out.split("\0") if f]
    r = _run(files, extra=("--min-files", "300"))
    assert r.returncode == 0, r.stderr[-3000:]
