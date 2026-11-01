"""The block a fresh conversation of a seat gets ahead of its first input (scripts/briefing_packet.py): its order,
the operator's messages verbatim with a flagged trim, the objective, records with ids, the repaired recent
conversation, cuts that never split a character, the budget from the window, and a wording that never names what
happened. The store is a stand-in with the contract's readers."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import briefing_packet as BP  # noqa: E402

SEAT = "qa-demo"


def row(uuid, role, text, kind="message", tool_id=None, ts="2026-10-31T07:00:00Z", part=0):
    return {"agent": SEAT, "session_id": "s-old", "generation": 3, "uuid": uuid, "part": part, "role": role,
            "kind": kind, "tool_id": tool_id, "text": text, "ts": ts}


class Store:
    def __init__(self, ops=None, recent=None, records=None, wakeups=None):
        self.ops = ops if ops is not None else [
            row("o1", "user", "From now on, always answer in British English and sign off with 'Cheers'."),
            row("o2", "user", "Build the Example quarterly report in docs/report.md with three charts."),
            row("o3", "user", "Which chart library are you using?"),
            row("o4", "user", "[DECISION ANSWERED] use the Sample palette"),
        ]
        self.recent = recent if recent is not None else [
            row("o2", "user", "Build the Example quarterly report in docs/report.md with three charts."),
            row("a1", "assistant", "Writing the report now.", part=0),
            row("a1", "assistant", 'Write {"file_path": "/work/docs/report.md", "content": "x"}', "tool_call",
                "toolu_w1", part=1),
            row("r1", "user", "File written", "tool_result", "toolu_w1"),
            row("a2", "assistant", 'Bash {"command": "make charts"}', "tool_call", "toolu_dangling"),  # no result
            row("o3", "user", "Which chart library are you using?"),
            row("a3", "assistant", "I'm using the Sample charting library. Two options: bars, or lines with markers."),
        ]
        self.records = records if records is not None else {
            "tasks": [{"id": "toolu_t1", "text": "Draw the third chart", "status": "in_progress"}],
            "approvals": [{"id": "apr_sample1", "question": "Approve publishing the report?", "status": "pending"}]}
        self.wake = wakeups if wakeups is not None else [
            {"tool_id": "toolu_cron1", "tool": "CronCreate", "id": None, "schedule": "0 9 * * *",
             "prompt": "check the report inbox"}]
        self.window_asked = None

    def operator_messages(self, agent):
        return list(self.ops)

    def recent_window(self, agent, budget_chars):
        self.window_asked = budget_chars
        return list(self.recent)

    def open_records(self, agent):
        return self.records

    def wakeups(self, agent):
        return list(self.wake)


def test_the_block_has_its_sections_in_order_wrapped_and_with_ids():
    out = BP.build_packet(SEAT, window_tokens=200_000, store=Store())
    t = out["text"]
    assert t.startswith(BP.OPEN + "\n") and t.endswith("\n" + BP.CLOSE)
    heads = ["orchestra memory search", "## The operator's messages", "## What you are working on now",
             "## Open tasks, approval cards and scheduled wakeups", "## The latest part of the conversation",
             "## Work steps"]
    at = [t.index(h) for h in heads]
    assert at == sorted(at), "header, operator messages, objective, records, conversation, steps"
    # operator messages verbatim, oldest first, with ids
    assert t.index("[o1 · ") < t.index("[o2 · ") < t.index("[o3 · ")
    assert "always answer in British English and sign off with 'Cheers'." in t
    # the objective is the latest directive: not the question, not the relayed decision line
    obj = t.split("## What you are working on now\n", 1)[1].split("\n\n", 1)[0]
    assert obj.startswith("[o2 · ") and "Example quarterly report" in obj
    # records with ids
    assert "task [toolu_t1] Draw the third chart (in_progress)" in t
    assert "approval card [apr_sample1] Approve publishing the report? (pending)" in t
    assert "scheduled wakeup [toolu_cron1] CronCreate: 0 9 * * * check the report inbox" in t
    # the conversation repaired: the dangling call is gone, the answered one kept as a work step
    assert "toolu_dangling" not in t and out["counts"]["repaired"] >= 1
    assert "[toolu_w1] Write ok file /work/docs/report.md" in t
    assert "[a3] assistant: I'm using the Sample charting library. Two options" in t


def test_its_wording_never_names_what_happened():
    out = BP.build_packet(SEAT, window_tokens=1_000_000, store=Store())
    assert BP.banned_words(out["text"]) == []
    for w in ("session", "Sessions", "rotation", "rotated", "Rotate", "candidate", "compaction", "COMPACT", "compacted",
              "handoff", "hand-off", "successor", "predecessor", "generation", "refresh", "context refresh", "briefing",
              "packet", "reset"):
        assert BP.banned_words(f"a {w} b") != [], w
    assert BP.banned_words("compactly, resetting, generational") == [], "whole words only"
    assert BP.STATUS_COMMAND in out["text"], "the header names the status command"
    assert BP.banned_words("run `orchestra memory compaction-status` now") == [], "that exact command only"
    assert BP.banned_words("see compaction-status") == ["compaction"], "nothing else"
    # the header and the section titles are ours: none of them may carry a banned word
    empty = BP.build_packet(SEAT, window_tokens=None, store=Store(ops=[], recent=[], records={}, wakeups=[]))
    assert BP.banned_words(empty["text"]) == []


def test_the_operator_messages_are_trimmed_oldest_first_and_the_trim_is_said():
    ops = [row(f"o{i}", "user", f"Instruction number {i}: " + "x" * 400) for i in range(200)]
    out = BP.build_packet(SEAT, window_tokens=40_000, store=Store(ops=ops))
    t = out["text"]
    assert out["trimmed"]["operator_messages"] > 0
    assert f"({out['trimmed']['operator_messages']} older messages not shown here: search for them.)" in t
    assert "[o199 · " in t and "[o0 · " not in t, "the newest kept, the oldest dropped first"
    assert out["chars"] <= out["budget_chars"] + len(BP.OPEN) + len(BP.CLOSE) + 2


def test_the_budget_follows_the_window_and_is_capped():
    assert BP.budget_chars(100_000) == 20_000 * 4
    assert BP.budget_chars(10_000_000) == BP.CAP_TOKENS * 4, "capped at 50k tokens"
    assert BP.budget_chars(10_000) == BP.MIN_TOKENS * 4
    assert BP.budget_chars(None) == BP.CAP_TOKENS * 4
    s = Store()
    BP.build_packet(SEAT, window_tokens=100_000, store=s)
    assert s.window_asked == int(80_000 * BP.SHARE_RECENT)


def test_the_latest_messages_stay_whole_or_are_cut_head_and_tail_never_inside_a_character():
    huge = "Start of the long answer. " + ("🧑‍🚀 crew 👍🏽 é" * 4000) + " End of the long answer."
    recent = [row("u1", "user", "Tell me everything."), row("a1", "assistant", huge)]
    out = BP.build_packet(SEAT, window_tokens=40_000, store=Store(recent=recent))
    t = out["text"]
    assert "Start of the long answer." in t and "End of the long answer." in t and "characters not shown" in t
    assert "[u1] user: Tell me everything." in t
    t.encode("utf-8")                                         # no lone surrogate
    # a cut never lands inside a cluster: no joiner, skin tone or combining mark right after the cut note
    for piece in t.split("characters not shown …]\n")[1:]:
        assert piece[0] not in "‍️" and not (0x1F3FB <= ord(piece[0]) <= 0x1F3FF)
    for before in t.split("\n[… ")[:-1]:
        assert before[-1] != "‍"
    s = "ab👍🏽cd"
    assert BP.safe_cut(s, 3) == 2 and BP.safe_cut(s, 4) == 4, "never between a thumb and its skin tone"
    assert BP.safe_cut("a🧑‍🚀b", 3) == 1, "never right after a joiner"


def test_a_question_or_a_notification_is_not_the_objective():
    assert not BP.is_directive("What is the plan?")
    assert not BP.is_directive("could you check the logs")
    assert not BP.is_directive("[TURN INTERRUPTED] the process stopped")
    assert not BP.is_directive("📬 New message from pm-demo")
    assert BP.is_directive("Ship the Sample report by Friday.")


def test_it_ends_with_the_standing_instruction_and_carries_only_checked_facts():
    out = BP.build_packet(SEAT, window_tokens=200_000, store=Store())
    body = out["text"][len(BP.OPEN) + 1:-len(BP.CLOSE) - 1]
    assert body.endswith(BP.CONTINUE)
    assert "Never invent." in BP.CONTINUE and "orchestra memory search" in BP.CONTINUE
    assert "- file changed: /work/docs/report.md [toolu_w1]" in out["text"]
    assert BP.checked_facts([{"text": "a fact with an id", "id": "u-1"}, {"text": "made up", "id": "nope"},
                             {"text": "no id at all"}], {"u-1"}) == ["- a fact with an id [u-1]"]


def test_the_check_asks_only_single_answer_questions_with_a_recorded_value_and_grades_each():
    out = BP.build_packet(SEAT, window_tokens=200_000, store=Store())
    qs = out["check"]["questions"]
    assert [(q["kind"], q["expected"]) for q in qs] == [
        ("last_message", "[DECISION ANSWERED] use the Sample palette"),
        ("last_reply", "I'm using the Sample charting library."),
        ("last_file", "/work/docs/report.md"),
        ("card", "apr_sample1"),
        ("task", "Draw the third chart")], "no command was run: that one is not asked"
    msg = BP.check_message(qs)
    assert "## Q1" in msg and "/work/docs/report.md" not in msg and "apr_sample1" not in msg, "no answers in it"
    good = ("## Q1\n\u201c[DECISION ANSWERED] Use the  Sample palette.\u201d\n"
            "## Q2\nI'm using the Sample charting library\n## Q3\n/work/docs/report.md\n"
            "## Q4\nThe card is apr_sample1.\n## Q5\nDraw the third chart")
    g = BP.grade_check(good, out["check"])
    assert g["pass"] and [r["pass"] for r in g["results"]] == [True, True, True, True, True]
    bad = BP.grade_check(good.replace("apr_sample1", "apr_sample12").replace("report.md", "notes.md"), out["check"])
    assert bad["missing"] == ["last_file", "card"]
    assert bad["results"][2] == {"kind": "last_file", "question": "Name the last file you wrote or edited (its full path).",
                                 "expected": "/work/docs/report.md", "answer": "/work/docs/notes.md", "pass": False}
    import headless_rotation as HR
    assert HR.plain_check_reason(bad) == "the check on the copy failed: it couldn't name the last file it wrote"
    empty = BP.build_packet(SEAT, window_tokens=None, store=Store(ops=[], recent=[], records={}, wakeups=[]))
    assert empty["check"] == {"questions": []}
    assert BP.grade_check("OK", empty["check"])["pass"] is False, "nothing checkable is never a pass"
    assert BP.grade_check("", empty["check"])["pass"] is False
    assert BP.banned_words(msg) == [] and BP.banned_words(BP.check_message([])) == []


def test_a_long_last_message_is_asked_for_its_first_sentence():
    long = "Please rebuild the Example index today. " + "More detail follows here. " * 40
    qs = BP.check_questions([row("o9", "user", long)], [], [], {}, [])
    assert qs[0]["q"].startswith("Quote the first sentence") and qs[0]["expected"] == "Please rebuild the Example index today."


def bash(uuid, tool_id, command, result="done", part=1, ts="2026-10-31T07:30:00Z"):
    return [row(uuid, "assistant", "Bash " + json.dumps({"command": command}), "tool_call", tool_id, ts=ts, part=part),
            row(uuid + "-r", "user", result, "tool_result", tool_id, ts=ts)]


def body_of(out):
    return out["text"][len(BP.OPEN) + 1:-len(BP.CLOSE) - 1]


def asked_values_are_in_the_body(out):
    for q in out["check"]["questions"]:
        assert q["expected"] in body_of(out), (q["kind"], q["expected"])


def test_a_shell_steps_command_is_on_its_line_and_its_question_is_answerable():
    s = Store()
    s.recent = s.recent + bash("a9", "toolu_b1", "ls -la && cat plan.md", "plan.md: 3 steps")
    out = BP.build_packet(SEAT, window_tokens=200_000, store=s)
    assert "- [toolu_b1] Bash ok $ ls -la && cat plan.md: …plan.md: 3 steps" in out["text"]
    q = [q for q in out["check"]["questions"] if q["kind"] == "last_command"]
    assert q and q[0]["expected"] == "ls -la && cat plan.md"
    asked_values_are_in_the_body(out)


def test_every_asked_question_is_answerable_from_the_block():
    long_cmd = "python3 -c '" + "x = 1; " * 80 + "'"
    fixtures = [Store(),
                Store(recent=Store().recent + bash("a9", "toolu_b2", long_cmd)),                 # over the cap
                Store(recent=Store().recent + bash("a9", "toolu_b3", "make one\nmake two")),     # two lines
                Store(ops=[row("o1", "user", "Please rebuild the Example index today. " + "More detail. " * 300)])]
    for s in fixtures:
        asked_values_are_in_the_body(BP.build_packet(SEAT, window_tokens=200_000, store=s))
    # a step the budget leaves out: many big steps, the last Bash one among them pushed out of its share
    many = Store().recent + [x for i in range(60) for x in bash(f"s{i}", f"toolu_s{i}", f"echo step {i}",
                                                              "out " * 200, ts=f"2026-10-31T07:{i:02d}:00Z")]
    many += [row("w-late", "assistant", 'Write {"file_path": "/work/docs/late.md", "content": "x"}', "tool_call",
                 "toolu_wl", ts="2026-10-31T08:00:00Z", part=1),
             row("w-late-r", "user", "ok", "tool_result", "toolu_wl", ts="2026-10-31T08:00:00Z")]
    out = BP.build_packet(SEAT, window_tokens=40_000, store=Store(recent=many))
    asked_values_are_in_the_body(out)
    assert "[toolu_s0]" not in out["text"], "the oldest steps did not fit"


def test_a_command_over_the_cap_is_shown_cut_and_never_asked():
    long_cmd = "python3 -c '" + "x = 1; " * 80 + "'"
    out = BP.build_packet(SEAT, window_tokens=200_000, store=Store(recent=Store().recent + bash("a9", "toolu_b2", long_cmd)))
    line = next(ln for ln in out["text"].splitlines() if ln.startswith("- [toolu_b2]"))
    assert " […] " in line and len(line) < BP.COMMAND_CAP + 120
    assert "last_command" not in [q["kind"] for q in out["check"]["questions"]]
    multi = BP.build_packet(SEAT, window_tokens=200_000,
                            store=Store(recent=Store().recent + bash("a9", "toolu_b3", "make one\nmake two")))
    assert "$ make one ↵ make two" in multi["text"], "one step per line, its newlines marked"
    assert "last_command" not in [q["kind"] for q in multi["check"]["questions"]]


def test_commands_and_output_go_through_the_index_redaction():
    import episodic_memory as em
    s = Store(recent=Store().recent + bash("a9", "toolu_b4", "curl -H 'Authorization: Bearer abcdefghijklmnop1234567890' "
                                                     "https://example.test/?token=s3cr3tvalue123",
                                           "key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"))  # pragma: allowlist secret
    s.redact = em.redact
    out = BP.build_packet(SEAT, window_tokens=200_000, store=s)
    line = next(ln for ln in out["text"].splitlines() if ln.startswith("- [toolu_b4]"))
    assert "s3cr3tvalue123" not in line and "abcdefghijklmnop1234567890" not in line and "sk-ant-api03-abc" not in line  # pragma: allowlist secret
    q = [q for q in out["check"]["questions"] if q["kind"] == "last_command"]
    assert q and "s3cr3tvalue123" not in q[0]["expected"] and q[0]["expected"] in body_of(out), \
        "the question asks for what the block shows, redacted"


def test_a_command_with_a_banned_word_never_fails_the_wording_check():
    s = Store(recent=Store().recent + bash("a9", "toolu_b5", "orchestra rotate --session pm-demo-reset",
                                           "session generation handoff"))
    out = BP.build_packet(SEAT, window_tokens=200_000, store=s)
    assert BP.banned_words(out["text"]) != [], "the quoted command and output do say them"
    assert BP.banned_words(BP.wording_of(out["text"])) == [], "our own wording never does"
    assert BP.wording_of("## Work steps\n- [x] Bash ok $ rotate\n[a1] user: session") == "## Work steps"



def test_fewer_than_two_checkable_questions_is_too_thin_and_never_a_pass():
    one = {"questions": [{"kind": "card", "q": "What is the id of the pending approval card?",
                          "expected": "apr_sample1", "id": "apr_sample1"}]}
    g = BP.grade_check("## Q1\napr_sample1", one)
    assert g["pass"] is False and g["too_thin"]
    import headless_rotation as HR
    assert HR.plain_check_reason(g) == "there wasn't enough on record to check the compacted chat, so nothing was changed"
    thin = BP.build_packet(SEAT, window_tokens=200_000, store=Store(ops=[], recent=[], records={}, wakeups=[]))
    assert BP.thin_check(thin) == {"asked": [], "not_asked": []}
    full = BP.build_packet(SEAT, window_tokens=200_000, store=Store())
    assert BP.thin_check(full) is None
    two = {"questions": one["questions"] + [{"kind": "task", "q": "What is the title of the latest open task?",
                                              "expected": "Draw the third chart", "id": "t1"}]}
    assert BP.grade_check("## Q1\napr_sample1\n## Q2\nDraw the third chart", two)["pass"] is True
    failed = BP.grade_check("## Q1\napr_sample1\n## Q2\nsomething else", two)
    assert failed["pass"] is False and failed["missing"] == ["task"] and not failed.get("too_thin")


def test_the_last_replys_first_sentence_is_asked_only_when_it_says_something():
    assert [q["kind"] for q in BP.check_questions([], [{"id": "a1", "role": "assistant", "content": "Done."}],
                                                  [], {}, [])] == []
    qs = BP.check_questions([], [{"id": "a1", "role": "assistant",
                                  "content": "The Example report now has five sections. More below."}], [], {}, [])
    assert qs == [{"kind": "last_reply", "q": "Quote the first sentence of your last reply, word for word.",
                   "expected": "The Example report now has five sections.", "id": "a1"}]
