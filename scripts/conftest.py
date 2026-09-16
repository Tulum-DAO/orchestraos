"""Test-run isolation for the addressability shadow log.

A test run must NEVER write into production evidence: my own pytest runs put 47
fixture lines (senders 'pm-x', 'a', 'svc') into logs/addressability-shadow.log
within minutes of the feature landing — and that log is precisely what gm reads to
decide whether arming is safe. A census polluted by test senders is worse than no
census, because it looks like data.
"""
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

# Fleet-safety belt (gm msg_bfb82953, 2026-09-16): no test may open a socket to the LIVE
# arturo-proxy :5071 -- a repo-root run POSTed fixture /finalize-call ids into the live
# proxy log and tripped the :5071 respawn guard. Autouse fixture; see hermetic_5071.py.
from hermetic_5071 import no_live_arturo_5071  # noqa: F401,E402

_TMP = Path(tempfile.gettempdir()) / "addressability-shadow-TESTS.log"
os.environ.setdefault("ADDRESSABILITY_LOG_FILE", str(_TMP))
# Same class, second seam (gm msg_7df59ba0): logs/menu-reconcile.log is the
# diagnostic evidence for the stale-card reconciler; a pytest run used to stamp
# fake REFUSED (resolve_cap/mass_loss/empty_scan) lines into it.
_TMP_RECONCILE = Path(tempfile.gettempdir()) / "menu-reconcile-TESTS.log"
os.environ.setdefault("MENU_RECONCILE_LOG_PATH", str(_TMP_RECONCILE))
# Third seam of the same class: the approval-notify escalation log.
_TMP_ESCALATION = Path(tempfile.gettempdir()) / "notify-escalation-TESTS.log"
os.environ.setdefault("NOTIFY_ESCALATION_LOG_PATH", str(_TMP_ESCALATION))
# ...and its sidecar: a cron_backstop() call without tailnet_state_path must
# never write the prod state/notify-tailnet-state.json from a test run.
_TMP_TAILNET = Path(tempfile.gettempdir()) / "notify-tailnet-TESTS.json"
os.environ.setdefault("TAILNET_NOTIFY_STATE_PATH", str(_TMP_TAILNET))
# Fleet-safety belt (gm msg_3477cbfb, incident apr_07805db5): under ANY pytest
# run the resume path's REAL side-effect seams (msg_store send + live pane
# inject) refuse outright, so a test row naming a live agent can never deliver
# a "[DECISION ANSWERED]" digest into a real pane again. Tests exercise those
# paths through patched seams (which the guard does not touch).
os.environ.setdefault("APPROVAL_RESUME_BLOCK_REAL_SEAMS", "1")
# ...and answer telemetry: tests must not write the prod answer-telemetry.jsonl.
_TMP_ANSWER_TELEMETRY = Path(tempfile.gettempdir()) / "answer-telemetry-TESTS.jsonl"
os.environ.setdefault("ANSWER_TELEMETRY_PATH", str(_TMP_ANSWER_TELEMETRY))


# --- MANDATORY prod-registry hermeticity guard (gm msg_fcb0ec90, 2026-08-31) ---
# A rotation test leaked an always_on row `seat-g2` into PROD registry.json (a
# phantom that pages pulse forever and could become a recovery/spawn target). The
# root cause: rotate_agent.REGISTRY_PATH binds to ORCHESTRA_DIR which defaults to
# the PROD checkout, so a test that drives execute_rotation writes prod even when
# run from a worktree. This guard makes the hermetic seam MANDATORY: any test that
# mutates the prod registry FAILS (naming itself), rather than silently leaking.
_PROD_REGISTRY = Path(
    os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
) / "registry.json"


def _registry_fingerprint():
    try:
        return hashlib.sha256(_PROD_REGISTRY.read_bytes()).hexdigest()
    except OSError:
        return None  # absent -> nothing to protect (fingerprints compare equal)


@pytest.fixture(autouse=True)
def _guard_prod_registry_unmutated():
    """Fail any test that mutates the production registry.json. Snapshot before,
    compare after — a changed fingerprint means the test wrote the prod store
    instead of a sandbox path (repoint rotate_agent.REGISTRY_PATH to tmp_path)."""
    before = _registry_fingerprint()
    yield
    # Guard-run context (gm msg_141aff5e): the identity reconciler runs the fleet guards on a
    # LIVE fleet where a promote / regenerator write can legitimately change registry.json
    # under the run. It declares that with ORCH_GUARD_RUN=1 and we skip the COMPARE only —
    # the guards' own DB-truth assertions still run. Normal pytest runs are unchanged.
    if os.environ.get("ORCH_GUARD_RUN") == "1":
        return
    after = _registry_fingerprint()
    assert before == after, (
        f"HERMETICITY LEAK: this test mutated the PRODUCTION registry at "
        f"{_PROD_REGISTRY}. Rotation tests MUST repoint rotate_agent.REGISTRY_PATH "
        f"(and ORCHESTRA_DIR-derived store paths) to a tmp sandbox. A leaked "
        f"always_on row pages pulse forever and can become a recovery target.")
