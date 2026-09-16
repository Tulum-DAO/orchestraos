"""Process-wide os.environ must not leak between tests (gm msg_596aaadb, 2026-09-16):
15 tests passed in isolation and failed only in the full single-process run — approval_get_qnr
(CLI subprocess inherits os.environ), the arturo requires_bearer family (watch_gateway's
TOKEN_FILE derives from ORCHESTRA_DIR at load), boundary_delivery's REAL resolver — because
~20 test files assign ORCHESTRA_DIR / HOME / *_PATH with a raw os.environ[...] = ... and some
never restore. The root conftest snapshots os.environ before every test and restores it after.
These two tests run in file order: the first leaks deliberately, the second must not see it."""
import os

_SENTINEL = "ORCH_TEST_LEAK_SENTINEL"


def test_a_leaks_environ_on_purpose(tmp_path):
    os.environ[_SENTINEL] = "leaked"
    os.environ["ORCHESTRA_DIR"] = str(tmp_path)
    os.environ["HOME"] = str(tmp_path)
    assert os.environ[_SENTINEL] == "leaked"


def test_b_sees_a_clean_environ():
    assert _SENTINEL not in os.environ, "a previous test's os.environ write leaked into this one"
    assert os.environ.get("HOME", "").startswith("/home/"), os.environ.get("HOME")
    assert not os.environ.get("ORCHESTRA_DIR", "").startswith("/tmp/pytest"), os.environ.get("ORCHESTRA_DIR")
