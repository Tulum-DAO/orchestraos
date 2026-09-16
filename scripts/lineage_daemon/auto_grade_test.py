"""Stage C — auto-grade the successor readback in-loop (STRICT), task #4.

RED-first tests for auto_grade_successor(): the machine grader that lets the
T2 auto-rotation gate on a MACHINE grade (no human grader in the loop).

It invokes the live rotation_gate_manual grader (`grade <successor>
--predecessor-sid <sid> --strict`) over an INJECTED subprocess runner so these
tests are hermetic — no shell-out, no real agent, no transcript on disk.

Disposition mapping (grader CLI exit codes are the contract):
  exit 0 -> {"disposition":"PASS"}      graded PASS, strict artifact written
  exit 1 -> {"disposition":"FAIL"}      graded FAIL, artifact written (not strict-pass)
  exit 2 -> {"disposition":"REFUSED"}   harness/input defect, NO artifact

CALLER CONTRACT: only PASS (with a written strict artifact) may feed a
graduation. FAIL and REFUSED route to hold/card — returned CLEANLY, never raised.
"""
from scripts.lineage_daemon import auto_grade as AG


def _runner(exit_code, *, stdout="", stderr="", record=None):
    """A fake subprocess runner. Records the argv it was handed (so a test can
    assert --strict is present) and returns a tiny result-like object carrying
    the exit code + streams — the same surface a real subprocess.run(...) exposes."""
    class _Result:
        def __init__(self, rc, out, err):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    def run(argv):
        if record is not None:
            record.append(list(argv))
        return _Result(exit_code, stdout, stderr)

    return run


# ---- disposition mapping ------------------------------------------------------

def test_exit0_maps_to_pass():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0))
    assert out["disposition"] == "PASS"


def test_exit1_maps_to_fail():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(1))
    assert out["disposition"] == "FAIL"


def test_exit2_maps_to_refused():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(2))
    assert out["disposition"] == "REFUSED"


def test_unknown_exit_code_is_refused_not_pass():
    # Fail-safe: an unexpected exit code must NEVER be read as PASS (a graduation
    # gate that auto-kills must not graduate on an ambiguous grader result).
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(137))
    assert out["disposition"] == "REFUSED"


# ---- --strict is passed for unattended runs -----------------------------------

def test_strict_flag_passed_by_default():
    rec = []
    AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0, record=rec))
    assert rec, "runner was never invoked"
    assert "--strict" in rec[0]


def test_strict_flag_can_be_turned_off():
    rec = []
    AG.auto_grade_successor("x-g3", "abcdef0123", strict=False,
                            runner=_runner(0, record=rec))
    assert "--strict" not in rec[0]


def test_argv_targets_the_grader_with_sid_and_successor():
    rec = []
    AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0, record=rec))
    argv = rec[0]
    # grade <successor> --predecessor-sid <sid>
    assert "grade" in argv
    assert "x-g3" in argv
    assert "--predecessor-sid" in argv
    assert "abcdef0123" in argv
    # points at the live grader module
    assert any("rotation_gate_manual.py" in a for a in argv)


def test_transcript_path_used_when_given_instead_of_sid():
    rec = []
    AG.auto_grade_successor("x-g3", None, transcript="/tmp/pred.jsonl",
                            runner=_runner(0, record=rec))
    argv = rec[0]
    assert "--transcript" in argv
    assert "/tmp/pred.jsonl" in argv
    assert "--predecessor-sid" not in argv


# ---- D9: the successor provenance sid is threaded into the grade argv ----------

def test_successor_sid_threaded_into_argv():
    """D9: the successor's live session_id is passed via --successor-sid so
    record_readback stamps it as the immutable provenance sid in the artifact."""
    rec = []
    AG.auto_grade_successor("x-g3", "abcdef0123", successor_sid="sess-succ-1",
                            runner=_runner(0, record=rec))
    argv = rec[0]
    assert "--successor-sid" in argv
    assert "sess-succ-1" in argv


def test_no_successor_sid_omits_flag():
    """Backward-compat: no successor_sid => the flag is absent (legacy grade path)."""
    rec = []
    AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0, record=rec))
    assert "--successor-sid" not in rec[0]


def test_build_grade_argv_includes_successor_sid():
    argv = AG.build_grade_argv("x-g3", "abcdef0123", successor_sid="sess-1")
    assert "--successor-sid" in argv and "sess-1" in argv


# ---- artifact path returned + only PASS carries the graduate-able artifact ----

def test_pass_returns_artifact_path():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0))
    assert out["artifact"].endswith("x-g3.comprehension.json")


def test_refused_reports_no_artifact():
    # exit 2 writes NO artifact — the disposition must say so cleanly so the
    # caller routes to hold, not graduation.
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(2))
    assert out["disposition"] == "REFUSED"
    assert out.get("artifact_written") is False


def test_pass_marks_artifact_written():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0))
    assert out["artifact_written"] is True


def test_fail_marks_artifact_written():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(1))
    assert out["artifact_written"] is True


# ---- caller-facing helpers: gate on PASS-only ---------------------------------

def test_may_graduate_only_on_strict_pass():
    passd = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0))
    faild = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(1))
    refd = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(2))
    assert AG.may_graduate(passd) is True
    assert AG.may_graduate(faild) is False
    assert AG.may_graduate(refd) is False


def test_may_graduate_false_for_supervised_pass():
    # A PASS produced WITHOUT --strict is not an arming grade -> may not graduate.
    supd = AG.auto_grade_successor("x-g3", "abcdef0123", strict=False,
                                   runner=_runner(0))
    assert AG.may_graduate(supd) is False


# ---- never raises on a runner failure (fail toward REFUSED/hold) --------------

def test_runner_exception_is_refused_not_raised():
    def boom(argv):
        raise RuntimeError("subprocess blew up")
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=boom)
    assert out["disposition"] == "REFUSED"
    assert out.get("artifact_written") is False


def test_missing_locator_is_refused():
    # No sid AND no transcript -> the grader can't be pointed at anything.
    # Refuse cleanly (hold), never raise, never PASS.
    out = AG.auto_grade_successor("x-g3", None, runner=_runner(0))
    assert out["disposition"] == "REFUSED"
    assert out.get("artifact_written") is False


def test_disposition_carries_exit_code():
    out = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(1))
    assert out["exit_code"] == 1


# ---- grade_signal: adapt to dispatch_graduation's grade_of tri-state ----------

def test_grade_signal_strict_pass_is_PASS():
    passd = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(0))
    assert AG.grade_signal(passd) == "PASS"


def test_grade_signal_fail_is_FAIL():
    faild = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(1))
    assert AG.grade_signal(faild) == "FAIL"


def test_grade_signal_refused_is_None():
    refd = AG.auto_grade_successor("x-g3", "abcdef0123", runner=_runner(2))
    assert AG.grade_signal(refd) is None


def test_grade_signal_supervised_pass_is_None():
    # A non-strict PASS must NOT reach dispatch as a graduating PASS.
    supd = AG.auto_grade_successor("x-g3", "abcdef0123", strict=False,
                                   runner=_runner(0))
    assert AG.grade_signal(supd) is None
