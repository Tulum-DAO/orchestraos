"""Central config for the approval loop. Import these; never hard-code twice."""
import os
import sys
import tempfile
from pathlib import Path

# The ONE data-dir default is orchestra_cli.settings.data_dir (data-dir sweep S5); orchestra_cli
# lives in this file's checkout, appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402

#: The LIVE install: the data dir this checkout is CONFIGURED for (orchestra.toml [data] dir, else
#: the product default), deliberately ignoring ORCHESTRA_DIR, so the pytest fence below names the one
#: path a test must never open whatever the test's env says.
LIVE_ORCHESTRA_DIR = _data_dir(include_env=False)


def orchestra_dir() -> Path:
    """Resolved ON EVERY CALL, not frozen at import.

    `ORCHESTRA_DIR` used to be read once at import, which made `monkeypatch.setenv` inside a
    test LOOK like isolation while the path stayed pinned to the live tree. A test then wrote
    to the operator's live ledger (2026-10-05, and the same class on 2026-09-25). Lazy
    resolution is what makes the env override actually mean something."""
    return Path(os.environ.get("ORCHESTRA_DIR") or _data_dir())


def db_path() -> Path:
    return orchestra_dir() / "state" / "tasks.db"


LIVE_DB_PATH = LIVE_ORCHESTRA_DIR / "state" / "tasks.db"


def under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def refuse_live_db_under_pytest(path) -> None:
    """Raise if a TEST is about to open the operator's live approvals DB.

    A conftest guard only protects suites that load that conftest; this one travels with the
    code, so it also covers a bare `pytest some_file.py` from another root, a doctest, or a
    script a test shells out to. Two independent fences, because this class has now recurred."""
    if not under_pytest():
        return
    if str(path) == ":memory:":
        return
    try:
        real = os.path.realpath(str(path))
        same = real == os.path.realpath(str(LIVE_DB_PATH))
        tmp = os.path.realpath(tempfile.gettempdir())
    except OSError:
        same, real, tmp = True, str(path), ""      # cannot tell where it points: fail CLOSED
    # FAIL CLOSED: a test may only open an approvals DB under the temp dir (tmp_path, mkdtemp).
    # LIVE_DB_PATH names the dir this checkout is CONFIGURED for, but an operator's real ledger can
    # live elsewhere (a fleet checkout's own state/, an older default), and naming it here would
    # ship a personal path. Anything outside the temp dir may be production, so it is refused.
    outside_tmp = not tmp or not (real == tmp or real.startswith(tmp + os.sep))
    if same or outside_tmp:
        raise RuntimeError(
            f"a test tried to open an approvals DB outside the temp dir ({path}); it may be the "
            f"LIVE approvals DB (this checkout's is {LIVE_DB_PATH}).\n"
            "Pass an explicit path: ApprovalStore(db_path=str(tmp_path / 'tasks.db')).\n"
            "Note that setting ORCHESTRA_DIR works now too (the path resolves lazily), but an "
            "explicit db_path is clearer and cannot be defeated by import order.")


def __getattr__(name):
    """PEP 562. `ORCHESTRA_DIR` and `DB_PATH` stay readable as module attributes for the ~13
    existing consumers, but resolve lazily now — so `approval_config.DB_PATH` honours a
    monkeypatched env. A `from approval_config import DB_PATH` still binds once at the
    IMPORTER's import time, which is why approval_schema no longer does that.

    GOTCHA worth knowing before you rely on the laziness: `monkeypatch.setattr(mod, "DB_PATH", x)`
    writes a REAL module attribute, and on undo monkeypatch restores the value it read rather than
    deleting the attribute — so after any such test, `DB_PATH` is concrete and shadows this
    function for the rest of the session. Read `db_path()` when you need the resolved value."""
    if name == "ORCHESTRA_DIR":
        return orchestra_dir()
    if name == "DB_PATH":
        return db_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

NTFY_BASE = os.environ.get("NTFY_BASE", "http://localhost:9080")
NTFY_APPROVALS_TOPIC = os.environ.get("NTFY_APPROVALS_TOPIC", "approvals")
NTFY_ANSWERS_TOPIC = os.environ.get("NTFY_ANSWERS_TOPIC", "approval-answers")
NTFY_TOKEN_FILE = Path(os.environ.get("NTFY_TOKEN_FILE", os.path.expanduser("~/.config/jarvis/ntfy-token")))

DEFAULT_OPTIONS = ["approve", "deny", "hold"]
EXPIRY_HOURS = 24
WATCHDOG_MINUTES = 5          # re-fire an un-acked resume after this
WATCHDOG_MAX_ATTEMPTS = 3     # then escalate
# SLA spec 2026-08-14 (DEC-1786690995): inject within 1 min of the target going
# idle. Cheap refusals (busy/no-live-head) retry every cron beat; only REAL
# outcomes count toward WATCHDOG_MAX_ATTEMPTS. WATCHDOG_MINUTES above is the
# ack window for landed-but-unacked injects (re-inject spam guard).
WATCHDOG_RETRY_BEAT_SECONDS = 55   # cheap-retry throttle (~one cron beat)
ESCALATE_STUCK_MINUTES = 15        # wall-clock backstop (dead emitter / forever-busy)
ESCALATE_REPEAT_MINUTES = 30       # re-escalate no more often than this
# Resolved at import, as before — only DB_PATH needed to become lazy for the test
# fence, and widening that here would change behaviour nobody asked me to change.
CURSOR_FILE = orchestra_dir() / "state" / ".approval-listener-cursor"

# DEC-1786664626 Q4 (the operator-gated, card apr_10c0c829): whether a PENDING decision
# still expires after EXPIRY_HOURS. Answered/acted decisions NEVER expire (F2) —
# that already holds (expire_due only touches status='pending'). This flag only
# governs the PENDING side, behind a config toggle so the operator's answer flips ONE
# setting, not a rebuild. Default = current behavior (pending expires at 24h).
# Set EXPIRE_PENDING=0 (env) to make pending decisions never expire either.
# THE OPERATOR ANSWERED Q4 on 2026-08-13 23:51Z (apr_10c0c829, option 1): "Nothing expires — pending
# too". That card ended resume_failed so the ruling never reached this default (it stayed
# "1" while expire_due had no caller, so nothing expired by accident). Default now records
# the ruling: pending decisions do NOT expire unless EXPIRE_PENDING=1 is set explicitly
# (gm msg_86bcc168 item 4 by effect: 27/28 live pending rows were past expires_at).
EXPIRE_PENDING = os.environ.get("EXPIRE_PENDING", "0") not in ("0", "false", "False", "")

# Off-tailnet escalation (gm commission msg_c5757395, apr_321ad47b gap): ntfy is
# served tailnet-only, so a push to a phone that is OFF Tailscale dies silently.
# approval_notify checks the phone's tailscale peer and escalates to a FULL
# Telegram card when it has been off longer than the threshold. Host is matched
# against the peer's DNSName prefix / HostName. Unknown => treated as ON.
PHONE_TAILNET_HOST = os.environ.get("PHONE_TAILNET_HOST", "iphone172")
PHONE_OFFTAILNET_THRESHOLD_S = int(os.environ.get("PHONE_OFFTAILNET_THRESHOLD_S", "600"))
TAILSCALE_STATUS_TIMEOUT_S = int(os.environ.get("TAILSCALE_STATUS_TIMEOUT_S", "5"))
# Digest mode (gm msg_083b5273): when MORE than this many rows qualify for the
# full-card escalation on one beat, send ONE digest instead of a pile-on.
ESCALATION_DIGEST_MAX_SINGLES = int(os.environ.get("ESCALATION_DIGEST_MAX_SINGLES", "3"))

def ntfy_token() -> str:
    try:
        return NTFY_TOKEN_FILE.read_text().strip()
    except OSError:
        return ""
