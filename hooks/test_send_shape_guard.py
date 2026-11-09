#!/usr/bin/env python3
"""RED-first tests for the send-shape guard (gm ruling msg_999a3ecd, 2026-09-26).

BIDIRECTIONAL BY CONTRACT. gm's grading contract: every historical shape must be REFUSED, and
the false-positive cases must be PROVEN to pass — "prove the false-positive cases pass, or you
have shipped friction, not a control".
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import send_shape_guard as g  # noqa: E402


@pytest.fixture(autouse=True)
def _never_write_the_real_log(tmp_path, monkeypatch):
    """NO TEST MAY WRITE TO THE PRODUCTION LOG.

    Found by checking whether the log could do the job gm assigned it (ongoing false-block
    detector between grades): it could not, because THIS SUITE was writing to it. 315 lines,
    211 REFUSE / 104 ALLOW, and almost none of it fleet traffic — one `-k standalone_send` run
    added two more. A refusal RATE computed off that measures my test suite, not the fleet, and
    the denominator OI asked for was the thing being corrupted.

    Autouse so a new test cannot forget; the two tests that assert ON the log override this with
    their own path.
    """
    monkeypatch.setattr(g, "LOG_PATH", str(tmp_path / "isolated-ssg.log"))


def _verdict(cmd):
    """The importable decision: None = allow, str = the refusal reason."""
    return g.refusal_for({"tool_name": "Bash", "tool_input": {"command": cmd}})


# ─────────────────────────── RED: the six historical shapes ───────────────────────────
# Every one is a real command shape from the ledger. All six are compound lines; five of the
# six also suppressed the send's own output, which is why nobody saw the refusal.

HISTORICAL = [
    # 1-2. g3 / g4, 2026-09-22 — stray fragment under orchestra-builder's name, then again.
    ("g3_stray_under_OB_name",
     "python3 msg_store.py send --from orchestra-builder --to x --subject '' --body '' ; "
     "python3 msg_store.py send --from gm --to operator --subject real --body real"),
    ("g4_same_again_next_generation",
     "python3 msg_store.py send --from orchestra-builder --to x --body '' && "
     "python3 msg_store.py send --from gm --to operator --subject real --body real"),
    # 3. gm-g77, 2026-09-22 — prefix fragment with output suppressed.
    ("gm_g77_prefix_with_devnull",
     "python3 msg_store.py send --from pm-listmagic --to pm-listmagic --body-file /dev/null "
     "2>/dev/null ; python3 msg_store.py send --from gm --to operator --subject s --body b"),
    # 4-5. orchestra-builder, 2026-09-23 — twice in one shift, self-addressed, to /dev/null.
    ("ob_instance3_self_addressed_devnull",
     "python3 msg_store.py send --from orchestra-builder --to orchestra-builder >/dev/null ; "
     "python3 msg_store.py send --from gm --to devex-review --subject s --body b"),
    ("ob_instance4_same_shape_14h_later",
     "python3 msg_store.py send --from orchestra-builder --to orchestra-builder 2>/dev/null; "
     "python3 msg_store.py send --from gm --to devex-review --subject s --body b"),
    # 6. devex-review g8, 2026-09-25 and 2026-09-26 — mine, twice in one session.
    ("devex_instance5_placeholder_double_redirect",
     "python3 msg_store.py send --from gm-placeholder --to nobody --type reply --subject x "
     "--body y 2>/dev/null >/dev/null; python3 msg_store.py send --from devex-review --to gm "
     "--type reply --subject real --body-file /dev/stdin"),
    ("devex_instance6_incomplete_prefix",
     "python3 msg_store.py send --from orchestra-builder-placeholder --to nobody 2>/dev/null; "
     "python3 msg_store.py send --from devex-review --to orchestra-builder --subject s --body b"),
]


@pytest.mark.parametrize("name,cmd", HISTORICAL, ids=[h[0] for h in HISTORICAL])
def test_every_historical_shape_is_REFUSED(name, cmd):
    why = _verdict(cmd)
    assert why is not None, f"{name}: the guard ALLOWED a known-bad historical shape"


def test_a_lone_send_with_its_output_thrown_away_is_REFUSED():
    """The suppression half, on its own. Five of six instances hid the refusal this way —
    a guard you cannot see is a guard you are trusting blindly."""
    assert _verdict("python3 msg_store.py send --from gm --to operator --subject s --body b "
                    "2>/dev/null") is not None


def test_two_sends_on_one_line_is_REFUSED_even_when_both_look_legitimate():
    """The defect is the SHAPE, not the intent. Two sends on a line is the stray-fragment
    signature whether or not the author meant both."""
    assert _verdict("python3 msg_store.py send --from gm --to a --subject s --body b ; "
                    "python3 msg_store.py send --from gm --to b --subject s --body b") is not None


# ───────────────── GREEN: the false-positive cases gm required be proven ─────────────────

LEGAL = [
    ("standalone_send",
     "python3 msg_store.py send --from devex-review --to gm --subject s --body b"),
    ("standalone_send_with_quoted_heredoc_body_file",
     "python3 msg_store.py send --from devex-review --to gm --subject s "
     "--body-file /dev/stdin <<'EOF'\nline one\nEOF"),
    ("cd_then_send_the_dominant_legitimate_form",
     "cd ~/scripts/agent-orchestra && python3 msg_store.py send --from devex-review --to gm "
     "--subject s --body b"),
    ("standalone_ack", "python3 msg_store.py ack --message-id msg_abc123"),
    ("standalone_get", "python3 msg_store.py get --id msg_abc123"),
    ("ack_in_a_for_loop_gm_runs_these",
     "for i in a b c; do python3 msg_store.py ack --message-id $i >/dev/null; done"),
    ("inbox_read_piped_into_python",
     "python3 msg_store.py inbox --agent devex-review | python3 -c 'import sys; print(len(sys.stdin.read()))'"),
    ("acks_compound_with_a_read",
     "python3 msg_store.py ack --message-id m1; python3 msg_store.py inbox --agent devex-review"),
    ("no_msg_store_at_all", "git status --short && ls -la"),
]


@pytest.mark.parametrize("name,cmd", LEGAL, ids=[l[0] for l in LEGAL])
def test_legitimate_shapes_still_PASS(name, cmd):
    why = _verdict(cmd)
    assert why is None, f"{name}: guard refused a legitimate command — that is friction: {why}"


# ─────────────────────────── the guard's own failure modes ───────────────────────────

def test_fails_OPEN_on_unparseable_payload():
    """A guard that cannot run must never stop the fleet from sending mail."""
    assert g.refusal_for({"tool_name": "Bash"}) is None
    assert g.refusal_for({}) is None
    assert g.refusal_for({"tool_name": "Bash", "tool_input": {"command": None}}) is None


def test_non_bash_tools_are_not_our_business():
    assert g.refusal_for({"tool_name": "Write", "tool_input": {"command": "send ; send"}}) is None


def test_entrypoint_exits_2_on_refusal_and_0_on_allow(tmp_path):
    """The DEPLOYED form: exit code is the contract the harness reads, not the return value."""
    entry = os.path.join(HERE, "send_shape_guard.py")
    bad = json.dumps({"tool_name": "Bash", "tool_input": {"command":
        "python3 msg_store.py send --from a --to b 2>/dev/null ; python3 msg_store.py send --from c --to d"}})
    good = json.dumps({"tool_name": "Bash", "tool_input": {"command":
        "python3 msg_store.py send --from a --to b --subject s --body x"}})
    # The SUBPROCESS re-imports the module, so the autouse fixture cannot reach it — it reads
    # SSG_LOG from the environment instead. Without this the deployed-form test writes to the
    # PRODUCTION log, which is the contamination this suite exists not to cause.
    env = {**os.environ, "SSG_LOG": str(tmp_path / "subproc-ssg.log")}
    r_bad = subprocess.run([sys.executable, entry], input=bad, capture_output=True, text=True,
                           env=env)
    r_good = subprocess.run([sys.executable, entry], input=good, capture_output=True, text=True,
                            env=env)
    assert r_bad.returncode == 2, f"refusal must exit 2, got {r_bad.returncode}"
    assert "send" in r_bad.stderr.lower(), "the refusal must say what it refused"
    assert r_good.returncode == 0, f"allow must exit 0, got {r_good.returncode}"


# ───────── FALSE POSITIVE found by gm while GRADING this guard (msg_3b8daf1b) ─────────
# R1 counted the substring anywhere in the command, so a send whose own BODY quoted the phrase
# counted itself into a refusal. gm hit it composing the grade and had to route the body through
# a file; my first attempt to test the fix was itself blocked by the guard under test, which
# counted 8 "invocations" inside quoted example strings. A guard that cannot be discussed
# without a workaround is friction — the thing this guard was contracted NOT to ship.
# Fix: R1 counts invocations at real command positions (outside quotes), not substrings.

_P = "msg_store.py" + " send"          # split so this file cannot trip its own subject


def test_a_send_whose_BODY_QUOTES_the_phrase_is_ALLOWED_single_quotes():
    assert _verdict(f"python3 {_P} --from a --to b --subject s "
                    f"--body 'the bad line was: python3 {_P} --from x'") is None


def test_a_send_whose_BODY_QUOTES_the_phrase_is_ALLOWED_double_quotes():
    assert _verdict(f'python3 {_P} --from a --to b --subject s '
                    f'--body "quoting python3 {_P} here"') is None


def test_a_send_whose_HEREDOC_quotes_the_phrase_is_ALLOWED():
    assert _verdict(f"python3 {_P} --from a --to b --body-file /dev/stdin <<'EOF'\n"
                    f"the bad line was python3 {_P} --from x\nEOF") is None


def test_the_fix_did_NOT_blind_R1_two_REAL_invocations_still_refused():
    """The half that matters: quoting-awareness must not become a way to hide a real second
    send. Two unquoted invocations still refuse."""
    assert _verdict(f"python3 {_P} --from a --to b ; python3 {_P} --from c --to d") is not None


def test_the_fix_did_NOT_blind_R2_a_redirected_send_still_refused():
    assert _verdict(f"python3 {_P} --from a --to b --subject s --body x 2>/dev/null") is not None


# ───── the AFFECTED CLASS orchestra-builder enumerated (msg_17f9a2d2), found independently ─────
# gm and OB hit the same false positive within ten minutes, by different routes. OB named three
# concrete cases I had not enumerated, and the third is the one that matters most: the command
# you would use to AUDIT for the very shape this guard protects was itself refused.

def test_OB1_driving_the_guard_END_TO_END_from_bash_is_possible():
    """A guard that refuses its own harness cannot be tested at the layer it runs at. OB's
    command had FOUR occurrences and ZERO real invocations — all inside single-quoted JSON
    being piped into the hook as stdin."""
    payload = ("""python3 -c 'import json;print(json.dumps({"c":"%s ; %s"}))' """
               """| python3 <home>/.claude/hooks/send-shape-guard.py""") % (_P, _P)
    assert _verdict(payload) is None


def test_OB2_a_baton_block_quoting_the_syntax_TWICE_is_allowed():
    """Batons and vote-request mails quote the send syntax as INSTRUCTIONS. One example was
    always fine; two would have been refused."""
    assert _verdict("cat >> baton.md <<'EOF'\n"
                    f"example one: python3 {_P} --from a --to b\n"
                    f"example two: python3 {_P} --from c --to d\nEOF") is None


def test_OB3_the_AUDIT_command_for_this_very_shape_is_allowed():
    """`grep` for the guarded phrase is how a human would audit for it. Refusing the audit of a
    hazard is worse than not guarding it, because it removes the way you would find the rest."""
    assert _verdict(f'grep -rn "{_P}" scripts/ | head') is None
    assert _verdict(f"grep -rn '{_P}' scripts/") is None


# ───── orchestra-improvements' two findings (msg_875f099f), both reproduced then fixed ─────

_GT = chr(62)          # built, not literal, so this file cannot trip its own subject


def test_OI_FINDING1_an_arrow_inside_a_QUOTED_BODY_is_not_a_redirect():
    """LIVE FALSE BLOCK. OI's legitimate send was refused because its --body quoted a shadow-log
    line of the form `gm > b-fire-watcher`. I had made R1 quote-aware and left R2 reading raw
    text, so the guard refused precisely the messages ANALYSING the behaviour it guards."""
    body = f"the line was gm {_GT} b-fire-watcher and it is wrong"
    assert _verdict(f"python3 {_P} --from oi --to ob --subject s --body '{body}'") is None


def test_OI_FINDING1_a_body_DISCUSSING_a_redirect_by_name_is_allowed():
    assert _verdict(f"python3 {_P} --from oi --to ob --subject s "
                    f"--body 'they used 2{_GT}/dev/null to hide it'") is None


def test_OI_FINDING1_did_NOT_blind_R2_a_real_unquoted_redirect_still_refused():
    """The other half: quote-awareness must not become a way to hide a real redirect."""
    assert _verdict(f"python3 {_P} --from a --to b --subject s --body x 2{_GT}/dev/null") is not None


def test_OI_FINDING2_every_decision_is_RECORDED_and_the_command_text_is_NEVER_written(tmp_path,
                                                                                      monkeypatch):
    """A fail-open guard that logs nothing cannot be told apart from a guard that has been
    fail-open since noon. ALLOW is logged too, which is what gives a refusal rate a denominator.
    And the line carries a sha256 prefix, never the command — a send's arguments carry message
    bodies, and a guard log that became a secret carrier would be self-inflicted."""
    log = tmp_path / "ssg.log"
    monkeypatch.setattr(g, "LOG_PATH", str(log))
    secret_ish = "SUPERSECRETBODY"
    _verdict(f"python3 {_P} --from a --to b --subject s --body {secret_ish}")          # ALLOW
    _verdict(f"python3 {_P} --from a --to b ; python3 {_P} --from c --to d")           # REFUSE R1
    _verdict(f"python3 {_P} --from a --to b --body x 2{_GT}/dev/null")                 # REFUSE R2
    text = log.read_text()
    assert "ALLOW" in text, "an allow must be recorded — it is the denominator"
    assert "rule=R1" in text and "rule=R2" in text, "each refusal must name its rule"
    assert secret_ish not in text, "THE COMMAND TEXT MUST NEVER REACH THE LOG"
    assert "cmd_sha=" in text, "the line must carry a hash so two events can be correlated"
    assert len(text.strip().splitlines()) == 3, "one line per decision"


def test_OI_FINDING2_logging_failure_can_NEVER_change_a_verdict(monkeypatch):
    """A record must never cost a decision."""
    monkeypatch.setattr(g, "LOG_PATH", "/proc/definitely/not/writable/ssg.log")
    assert _verdict(f"python3 {_P} --from a --to b --subject s --body x") is None
    assert _verdict(f"python3 {_P} --from a --to b ; python3 {_P} --from c --to d") is not None


# ─── BYPASS PROBES: quote-awareness must never become a place to HIDE a real invocation ───
# gm ran this class adversarially against 87ab9df213 and found no bypass (msg_3a6696c5). That
# grade does NOT transfer: 1583c77f4a CHANGED R2 to be quote-aware too, so the predicate gm
# probed is not the predicate running now. Re-probed at the new sha and pinned, because a
# narrowed detector is exactly where a hole would appear.

_Q, _DQ = chr(39), chr(34)

BYPASS_PROBES = [
    ("redirect_right_after_a_quoted_arg",
     f"python3 {_P} --from a --to b --body {_Q}x{_Q} 2{_GT}/dev/null"),
    ("redirect_abutting_a_quoted_arg_no_space",
     f"python3 {_P} --from a --to b --body {_Q}x{_Q}2{_GT}/dev/null"),
    ("redirect_after_a_double_quoted_arg",
     f"python3 {_P} --from a --to b --body {_DQ}x{_DQ} {_GT}/dev/null"),
    ("quoted_arrow_THEN_a_real_redirect",
     f"python3 {_P} --from a --to b --body {_Q}gm {_GT} watcher{_Q} 2{_GT}/dev/null"),
    ("second_real_send_after_a_quoted_mention",
     f"echo {_Q}bad line was python3 {_P} --from x{_Q} ; python3 {_P} --from a --to b ; "
     f"python3 {_P} --from c --to d"),
    ("real_send_after_an_escaped_quote_inside_a_double_quoted_arg",
     f'python3 {_P} --from a --to b --body {_DQ}it said \\{_DQ} ok{_DQ} ; python3 {_P} --from c --to d'),
    ("and_joined_second_send_after_a_double_quoted_arg",
     f'python3 {_P} --from a --to b --body {_DQ}x{_DQ} && python3 {_P} --from c --to d'),
    ("unbalanced_quote_then_a_real_redirect",
     f"python3 {_P} --from a --to b --body {_Q}unclosed 2{_GT}/dev/null"),
]


@pytest.mark.parametrize("name,cmd", BYPASS_PROBES, ids=[b[0] for b in BYPASS_PROBES])
def test_quote_awareness_is_NOT_a_bypass(name, cmd):
    assert _verdict(cmd) is not None, (
        f"{name}: a REAL hazard slipped through the quote-aware narrowing — that is the hole "
        f"the narrowing was always at risk of opening")


# ─── OI's residual (msg_a7f01070): the record must JOIN across the fail-open boundary ───

def test_OI_RESIDUAL_fail_open_hashes_the_SAME_object_as_allow_and_refuse(tmp_path, monkeypatch):
    """The sha was offered IN PLACE OF the command text, so it has to actually correlate.

    FAIL_OPEN first hashed the stringified tool_input DICT, so the same command produced a
    different cmd_sha than ALLOW/REFUSE. The join it broke is the one somebody wants most: was
    the command that FAIL_OPENed the same one that REFUSED five minutes later — i.e. did the
    guard start working again on the same input?
    """
    log = tmp_path / "join.log"
    monkeypatch.setattr(g, "LOG_PATH", str(log))
    cmd = f"python3 {_P} --from a --to b --subject s --body x"
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}

    assert g.refusal_for(payload) is None                      # ALLOW, normal path

    def _boom(*_a, **_k):
        raise RuntimeError("induced")
    monkeypatch.setattr(g, "_unquoted_spans", _boom)           # force the fail-open path
    assert g.refusal_for(payload) is None                      # FAIL_OPEN, same command

    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2, lines
    shas = [next(t for t in ln.split() if t.startswith("cmd_sha=")) for ln in lines]
    assert "ALLOW" in lines[0] and "FAIL_OPEN" in lines[1]
    assert shas[0] == shas[1], (
        f"the two paths must produce the SAME identifier for the same command: {shas}")


def test_OI_RESIDUAL_a_row_that_cannot_join_is_LABELLED_not_silently_different():
    """When the command is not recoverable, the row says so. Driven on the extracted helper,
    because the branch is defensive and unreachable through the normal path — a branch you
    cannot reach is a branch you cannot grade, so it was extracted rather than contrived."""
    cmd, label = g.fail_open_fields({"tool_name": "Bash", "tool_input": {"command": 12345}},
                                    RuntimeError("x"))
    assert cmd == "" and "no_command" in label and "RuntimeError" in label

    class _Hostile(dict):
        def get(self, *a, **k):
            raise ValueError("hostile payload")
    cmd2, label2 = g.fail_open_fields(_Hostile(), KeyError("k"))
    assert cmd2 == "" and "no_command" in label2, "the helper must not raise on a hostile payload"

    good = f"python3 {_P} --from a --to b"
    cmd3, label3 = g.fail_open_fields({"tool_name": "Bash", "tool_input": {"command": good}},
                                      RuntimeError("x"))
    assert cmd3 == good and "no_command" not in label3, "a recoverable command must JOIN"


def test_THE_SUITE_ITSELF_NEVER_WRITES_THE_PRODUCTION_LOG(tmp_path):
    """The instrument must not be corrupted by the thing testing it.

    gm designated the log as the ongoing false-block detector BETWEEN grades. It could not do
    that job while this suite was writing to it: 315 lines, 211 REFUSE / 104 ALLOW, almost none
    of it fleet traffic. A refusal RATE computed off that measures the test suite, and the
    denominator OI asked for was the very thing being polluted.

    Two paths had to close: in-process (the autouse fixture) and the SUBPROCESS deployed-form
    test, which re-imports the module and so reads SSG_LOG from the environment instead.
    """
    real = os.path.expanduser("~/scripts/agent-orchestra/logs/send-shape-guard.log")
    before = os.path.getsize(real) if os.path.exists(real) else 0
    _verdict(f"python3 {_P} --from a --to b --subject s --body x")          # ALLOW
    _verdict(f"python3 {_P} --from a --to b ; python3 {_P} --from c --to d")  # REFUSE
    entry = os.path.join(HERE, "send_shape_guard.py")
    env = {**os.environ, "SSG_LOG": str(tmp_path / "sub.log")}
    subprocess.run([sys.executable, entry], env=env, text=True, capture_output=True,
                   input=json.dumps({"tool_name": "Bash", "tool_input": {"command":
                       f"python3 {_P} --from a --to b"}}))
    after = os.path.getsize(real) if os.path.exists(real) else 0
    assert after == before, (
        f"the suite wrote {after - before} bytes to the PRODUCTION log — that corrupts the "
        f"denominator the log exists to provide")
