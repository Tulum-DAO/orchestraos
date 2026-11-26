"""`approval.py pending --grep/--mine`: the "has the operator already been asked" check.

`pending` emits a large JSON blob (~190 KB for ~80 live cards). A seat under pressure free-text-greps
that blob, finds the card it wants FOURTH among many hits, concludes there is no card, and asks the
operator AGAIN -- which is how one exposure reached the operator five times in a day. One line per hit
is the fix, because the answer has to be readable without scrolling.

Fixtures, not live rows: the live queue changes under us, and a test that asserts on
today's cards is a test that rots. The SHAPE of the incident is reproduced instead -- a
token-rotation card plus two token-usage-dashboard cards that must still appear.

Hermetic: APPROVAL_DB_PATH, ORCHESTRA_DIR and OUTWARD_LEDGER_PATH all point into tmp_path; ntfy fenced.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "approval.py")


@pytest.fixture()
def env(tmp_path):
    e = dict(os.environ)
    e["APPROVAL_DB_PATH"] = str(tmp_path / "approvals.db")
    e["ORCHESTRA_DIR"] = str(tmp_path / "orch")
    e["OUTWARD_LEDGER_PATH"] = str(tmp_path / "outward-actions.db")
    e["NTFY_TOKEN_FILE"] = str(tmp_path / "NO_SUCH_TOKEN")
    e["WATCH_GATEWAY_URL"] = "http://127.0.0.1:9"
    # The CLI's interposer guard reads the caller's seat from TMUX_PANE; run from inside a seat, the fixture
    # cards (authored as other seats) would be REFUSED. Outside a pane the guard has no caller and allows.
    e.pop("TMUX_PANE", None)
    e.pop("TMUX", None)
    return e


def _file(env, question, *, feature=None, summary=None, op_key=None):
    args = [sys.executable, CLI, "request", "--from", "orchestra-improvements",
            "--worker-kind", "pane", question]
    if feature:
        args += ["--feature", feature]
    if summary:
        args += ["--summary", summary]
    if op_key:
        args += ["--op-key", op_key]
    p = subprocess.run(args, capture_output=True, text=True, env=env, cwd=HERE, timeout=90)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip().splitlines()[-1]


def _pending(env, *flags):
    p = subprocess.run([sys.executable, CLI, "pending", *flags],
                       capture_output=True, text=True, env=env, cwd=HERE, timeout=90)
    assert p.returncode == 0, p.stderr
    return p.stdout


@pytest.fixture()
def queue(env):
    """gm's shape: the card someone needs, plus near-miss cards that must still show."""
    ids = {
        "rotate": _file(env, "Rotate the Telegram bot token exposed in transcripts",
                        feature="security / exposure", op_key="k-rotate"),
        "dash1": _file(env, "Send the partner the interactive token-usage dashboard",
                       feature="commitment", op_key="k-dash1"),
        "dash2": _file(env, "Share the token-usage breakdown when he is back",
                       feature="commitment", op_key="k-dash2"),
        "unrelated": _file(env, "Approve the retreat pricing tiers",
                           feature="Retreats", op_key="k-other"),
    }
    return ids


def test_grep_finds_the_card_and_keeps_the_near_misses_one_line_each(env, queue):
    """gm's RED, both halves: the token card IS found, and the token-usage-dashboard
    cards are still listed -- at one line each."""
    out = _pending(env, "--grep", "token")
    lines = [l for l in out.splitlines() if l.strip()]
    assert queue["rotate"] in out, "the card the caller needs was not found"
    assert queue["dash1"] in out and queue["dash2"] in out, "near-miss cards must still list"
    assert queue["unrelated"] not in out, "a non-matching card must not list"
    assert len(lines) == 3, f"expected ONE line per hit (3), got {len(lines)}: {lines}"
    for l in lines:
        assert l.count("|") == 3, f"line must be id | from | feature | question: {l!r}"


def test_a_multiline_question_still_prints_as_ONE_line(env):
    """The point of the feature is scannability; an embedded newline defeats it."""
    rid = _file(env, "Line one about a token\nline two\n\nline three", op_key="k-multi")
    lines = [l for l in _pending(env, "--grep", "token").splitlines() if l.strip()]
    assert len(lines) == 1, f"a multi-line question produced {len(lines)} lines"
    assert rid in lines[0]
    assert "line two" in lines[0] and "line three" in lines[0], "content must not be dropped"


def test_grep_is_case_insensitive_and_searches_summary_and_feature(env):
    a = _file(env, "Nothing notable here", feature="Token Usage", op_key="k-feat")
    b = _file(env, "Also nothing", summary="mentions a TOKEN in the summary", op_key="k-summ")
    out = _pending(env, "--grep", "token")
    assert a in out, "feature was not searched"
    assert b in out, "summary was not searched"
    out_upper = _pending(env, "--grep", "TOKEN")
    assert a in out_upper and b in out_upper, "search must be case-insensitive"


def test_no_match_says_the_operator_has_NOT_been_asked_on_stderr(env, queue):
    """Fails toward a statement, not silence: an empty stdout is ambiguous between
    'no card' and 'the flag is broken'."""
    p = subprocess.run([sys.executable, CLI, "pending", "--grep", "zzz-nomatch"],
                       capture_output=True, text=True, env=env, cwd=HERE, timeout=90)
    assert p.returncode == 0
    assert p.stdout.strip() == ""
    assert "has NOT been asked" in p.stderr


def test_mine_filters_by_author_and_tolerates_a_rotation_alias(env, queue):
    out = _pending(env, "--mine", "orchestra-improvements")
    assert queue["rotate"] in out and queue["unrelated"] in out
    # -gN/-genN must collapse: this seat's own _canon fix, reused here
    out_alias = _pending(env, "--mine", "orchestra-improvements-g8")
    assert queue["rotate"] in out_alias, "--mine must tolerate a rotation alias"
    out_other = _pending(env, "--mine", "some-other-seat")
    assert queue["rotate"] not in out_other


def test_mine_and_grep_compose(env, queue):
    out = _pending(env, "--mine", "orchestra-improvements", "--grep", "token")
    assert queue["rotate"] in out
    assert queue["unrelated"] not in out


def test_the_DEFAULT_output_is_still_the_JSON_the_dashboard_reads(env, queue):
    """The dashboard and the gateway read this feed. Adding a flag must not change the
    default contract -- pin it, so a later 'tidy-up' that makes lines the default is RED."""
    raw = _pending(env)
    rows = json.loads(raw)
    assert isinstance(rows, list) and len(rows) == 4
    for k in ("id", "from_agent", "question", "options", "status", "created_at",
              "kind", "summary", "menu", "risk_level", "reversibility", "feature"):
        assert k in rows[0], f"default JSON lost the {k!r} field"
    assert json.loads(_pending(env, "--json")) == rows, "--json must equal the default"


# ---- A pending human_task card and a pending questionnaire must not read as
# "the operator has NOT been asked" under --grep. MEASURED cause: --grep searched only question/summary/feature
# (not the card id, block_task, blocks_what or from_agent) and the questionnaire store was not searched at
# all. human_task rows WERE in the feed; the search fields and kinds were the gap.

def _human_task(env, task, *, blocks=None, op_key):
    args = [sys.executable, CLI, "human-task", "--from", "ios-watch-dev", "--task", task, "--op-key", op_key]
    if blocks:
        args += ["--blocks", blocks]
    p = subprocess.run(args, capture_output=True, text=True, env=env, cwd=HERE, timeout=90)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip().splitlines()[-1]


def _questionnaire(env, title, *, op_key):
    qs = json.dumps([{"prompt": "Which one?", "kind": "free_text"}])
    p = subprocess.run([sys.executable, CLI, "questionnaire", "--from", "macos-dev", "--title", title,
                        "--op-key", op_key, "--questions-json", qs],
                       capture_output=True, text=True, env=env, cwd=HERE, timeout=90)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip().splitlines()[-1]


def _rid(s):
    """The card id out of a CLI's last line (id alone, or JSON carrying it)."""
    import re
    m = re.search(r"(apr|qnr)_[0-9a-f]{8}_\d+", s)
    assert m, s
    return m.group(0)


def test_grep_finds_a_human_task_card_by_its_id_and_its_task_text(env):
    rid = _rid(_human_task(env, "Unlock your iPhone on Wi-Fi so I can pull the call log",
                           blocks="sizing the audio fix", op_key="k-ht1"))
    short = rid.split("_")[1]
    assert rid in _pending(env, "--grep", short), "a card must be findable by its id"
    assert rid in _pending(env, "--grep", "pull the call log"), "block_task text must be searched"
    assert rid in _pending(env, "--grep", "audio fix"), "blocks_what must be searched"


def test_grep_finds_any_card_by_id(env, queue):
    short = queue["unrelated"].split("_")[1]
    out = _pending(env, "--grep", short)
    assert queue["unrelated"] in out and queue["rotate"] not in out


def test_grep_and_mine_cover_pending_questionnaires(env):
    qid = _rid(_questionnaire(env, "Pick the retreat dates for the pilot", op_key="k-q1"))
    assert qid in _pending(env, "--grep", "retreat dates"), "a questionnaire title must be searched"
    assert qid in _pending(env, "--grep", qid.split("_")[1]), "a questionnaire must be findable by id"
    assert qid in _pending(env, "--mine", "macos-dev"), "--mine must cover questionnaires"
    lines = [l for l in _pending(env, "--grep", "retreat dates").splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0].count("|") == 3


def test_no_match_line_names_what_was_searched(env, queue):
    p = subprocess.run([sys.executable, CLI, "pending", "--grep", "zzz-nomatch"],
                       capture_output=True, text=True, env=env, cwd=HERE, timeout=90)
    for word in ("approval", "menu", "human_task", "questionnaire", "id"):
        assert word in p.stderr, f"the no-match line must say it searched {word!r}: {p.stderr!r}"


def test_grep_with_json_prints_only_the_matching_cards_as_json(env, queue):
    """Re-review of #642: --json with --grep printed the WHOLE feed, so `| jq length` counted every card
    and a seat concluded the operator had already been asked."""
    rows = json.loads(_pending(env, "--grep", "retreat", "--json"))
    assert [r["id"] for r in rows] == [queue["unrelated"]]
    assert json.loads(_pending(env, "--grep", "no-such-thing-anywhere", "--json")) == []
