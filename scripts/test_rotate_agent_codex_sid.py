"""rotate_agent.extract_active_sid finds a Codex seat's session in the layout Codex really writes.

Codex stores sessions as ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl (the public
lineage resolver, scripts/lineage_daemon/wal/ctx_adapters.py, globs exactly that). The rotate
lookup globbed sessions/*/*.jsonl (two levels) and returned the file stem, so it could never
match, and every rotated Codex successor was recorded as "unverifiable-sid"
(rotate_agent.py: `succ_sid = extract_active_sid(succ_alias, runtime) or "unverifiable-sid"`).
"""
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
UUID_A = "00000000-0000-7000-8000-00000000000a"
UUID_B = "00000000-0000-7000-8000-00000000000b"


@pytest.fixture
def ra(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    spec = importlib.util.spec_from_file_location("rotate_agent_codex_sid", HERE / "rotate_agent.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "ORCHESTRA_DIR", data, raising=False)
    monkeypatch.setattr(m, "get_agent_session", lambda agent_id: {})
    monkeypatch.setattr(m, "_sid_from_pane_events", lambda session_name: None)
    return m


def _rollout(home, day, uuid, seat, age_s, *, subagent_of=None, boot=True, extra_rows=None,
             raw_lines=None, meta_broken=False):
    """One rollout in the shape Codex 0.153 really writes (measured on this box): session_meta
    first, with an instructions payload of ~19 KB; then developer messages; then the first user
    message, <environment_context>; then the seat's boot line as a LATER user message, ~70 KB in.
    A subagent rollout carries the PARENT's boot line and is marked only in session_meta."""
    d = home / ".codex" / "sessions" / "2026" / "10" / day
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-2026-10-{day}T08-00-00-{uuid}.jsonl"
    meta = {"id": uuid, "cwd": "/home/user/orchestraos", "cli_version": "0.153.4",
            "base_instructions": "x" * 19000}
    if subagent_of:
        meta.update({"thread_source": "subagent", "source": {"subagent": {"thread_spawn": {}}},
                     "parent_thread_id": subagent_of})
    def msg(role, text):
        return {"type": "response_item", "payload": {"type": "message", "role": role,
                                                     "content": [{"type": "input_text", "text": text}]}}
    rows = [{"type": "session_meta", "payload": meta},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            msg("developer", "<skills_instructions>" + "y" * 49000 + "</skills_instructions>"),
            msg("developer", "You are `/root`, the primary agent in a multi-agent session."),
            msg("user", "<environment_context>\n  <cwd>/home/user/orchestraos</cwd>\n</environment_context>")]
    if boot:
        rows.append(msg("user", f"You are {seat}, a seat in OrchestraOS. Read your prompt and start."))
    rows.extend(extra_rows or [])
    lines = [json.dumps(r) for r in rows]
    if meta_broken:
        lines[0] = lines[0][:40]                     # line 1 is not valid JSON
    lines = (raw_lines or []) + lines if raw_lines and raw_lines[0] is None else lines
    p.write_text("\n".join(lines) + "\n")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def test_a_codex_seats_session_is_found_in_the_real_rollout_layout(ra, tmp_path):
    home = tmp_path / "home"
    _rollout(home, "13", UUID_A, "dev-codex-g2", age_s=60)
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A


def test_the_uuid_is_returned_not_the_file_stem(ra, tmp_path):
    home = tmp_path / "home"
    _rollout(home, "13", UUID_A, "dev-codex-g2", age_s=60)
    sid = ra.extract_active_sid("dev-codex-g2", "codex")
    assert sid == UUID_A and not sid.startswith("rollout-")


def test_another_seats_newer_rollout_is_not_taken(ra, tmp_path):
    """Identity, not recency: a different seat's newer rollout must not be returned for ours."""
    home = tmp_path / "home"
    _rollout(home, "12", UUID_A, "dev-codex-g2", age_s=3600)
    _rollout(home, "13", UUID_B, "pm-codex-g5", age_s=10)
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A
    assert ra.extract_active_sid("pm-codex-g5", "codex") == UUID_B


def test_no_rollout_for_the_seat_means_none_not_a_guess(ra, tmp_path):
    home = tmp_path / "home"
    _rollout(home, "13", UUID_B, "pm-codex-g5", age_s=10)
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None


def test_a_seat_name_is_a_whole_token_so_codex_gm_never_takes_codex_gm_g7s_session(ra, tmp_path):
    """Substring identity collided on real seat names (orchestraos-builder, #429 review)."""
    home = tmp_path / "home"
    _rollout(home, "12", UUID_A, "codex-gm", age_s=3600)
    _rollout(home, "13", UUID_B, "codex-gm-g7", age_s=10)        # newer, and contains "codex-gm"
    assert ra.extract_active_sid("codex-gm", "codex") == UUID_A
    assert ra.extract_active_sid("codex-gm-g7", "codex") == UUID_B


def test_a_subagent_rollout_is_skipped_even_though_it_carries_the_parents_boot_line(ra, tmp_path):
    """Codex writes the subagent's rollout next to the parent's, newer, with the parent's boot
    line as its first user message; only session_meta marks it (same rule as the live resolver)."""
    home = tmp_path / "home"
    _rollout(home, "12", UUID_A, "dev-codex-g2", age_s=3600)
    _rollout(home, "13", UUID_B, "dev-codex-g2", age_s=10, subagent_of=UUID_A)
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A


def test_a_rollout_with_no_boot_line_is_not_a_match(ra, tmp_path):
    """<environment_context> is the first user message in every rollout; it names no seat."""
    _rollout(tmp_path / "home", "13", UUID_B, "dev-codex-g2", age_s=10, boot=False)
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None


def _umsg(text):
    return {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                 "content": [{"type": "input_text", "text": text}]}}


@pytest.mark.parametrize("other", ["codex-gm.ops", "codex-gm_2", "codex-gm-g7"])
def test_a_dot_underscore_or_dash_after_the_name_is_another_seat(ra, tmp_path, other):
    """codex peer review: `(?![\\w-])` accepted a dot as a boundary, so codex-gm.ops matched codex-gm."""
    home = tmp_path / "home"
    _rollout(home, "12", UUID_A, "codex-gm", age_s=3600)
    _rollout(home, "13", UUID_B, other, age_s=10)
    assert ra.extract_active_sid("codex-gm", "codex") == UUID_A
    assert ra.extract_active_sid(other, "codex") == UUID_B


def test_a_quoted_boot_phrase_in_a_later_message_never_establishes_identity(ra, tmp_path):
    """Only the first boot-shaped user message decides. Another seat's rollout that later QUOTES
    "You are dev-codex-g2 ..." (a person pasting, a review) must not become dev-codex-g2's session."""
    home = tmp_path / "home"
    _rollout(home, "13", UUID_B, "pm-codex-g5", age_s=10,
             extra_rows=[_umsg("please check this line: You are dev-codex-g2, a seat in OrchestraOS.")])
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None
    assert ra.extract_active_sid("pm-codex-g5", "codex") == UUID_B


def test_the_first_boot_line_decides_even_if_a_later_message_is_a_pasted_boot_line(ra, tmp_path):
    """pm-codex-g5's rollout later carries a message that IS a boot line for dev-codex-g2 (pasted
    verbatim, so it starts with "You are"). The first boot line named pm-codex-g5; that decided."""
    home = tmp_path / "home"
    _rollout(home, "13", UUID_B, "pm-codex-g5", age_s=10,
             extra_rows=[_umsg("You are dev-codex-g2, a seat in OrchestraOS. Read your prompt and start.")])
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None
    assert ra.extract_active_sid("pm-codex-g5", "codex") == UUID_B


def test_a_rollout_whose_metadata_cannot_be_read_is_not_ours(ra, tmp_path):
    """An unparseable line 1 cannot rule out a subagent thread, so the file is skipped (fail closed)."""
    _rollout(tmp_path / "home", "13", UUID_A, "dev-codex-g2", age_s=10, meta_broken=True)
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None


def test_odd_but_valid_json_records_never_crash_the_lookup(ra, tmp_path):
    home = tmp_path / "home"
    odd = [None, [], 1, {"type": "response_item", "payload": None},
           {"type": "response_item", "payload": {"type": "message", "role": "user", "content": None}},
           {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [None, {"text": 5}]}}]
    p = _rollout(home, "13", UUID_A, "dev-codex-g2", age_s=10)
    lines = p.read_text().split("\n")
    lines = [lines[0]] + [json.dumps(r) for r in odd] + lines[1:]
    p.write_text("\n".join(lines))
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A


def test_a_malformed_record_before_the_boot_line_means_not_ours(ra, tmp_path):
    """codex peer review: if another seat's own boot record is corrupt and skipped, a later pasted
    "You are dev-codex-g2, ..." would become the deciding line and hand over pm-codex-g5's session."""
    home = tmp_path / "home"
    p = _rollout(home, "13", UUID_B, "pm-codex-g5", age_s=10,
                 extra_rows=[_umsg("You are dev-codex-g2, a seat in OrchestraOS. Read your prompt and start.")])
    lines = p.read_text().split("\n")
    boot_i = next(i for i, l in enumerate(lines) if "You are pm-codex-g5" in l)
    lines[boot_i] = lines[boot_i][:60]                   # pm-codex-g5's own boot record is corrupt
    p.write_text("\n".join(lines))
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None
    assert ra.extract_active_sid("pm-codex-g5", "codex") is None
    _rollout(home, "12", UUID_A, "dev-codex-g2", age_s=3600)     # the older, readable candidate is found
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A


def test_bytes_that_are_not_utf8_make_a_record_unreadable_not_ours(ra, tmp_path):
    """codex peer review, round 4: errors="replace" would turn invalid bytes into U+FFFD and could
    hand a corrupt session_meta or boot record to the parser as if it were sound. Strict decoding:
    such a record is unreadable, and the file is not ours."""
    home = tmp_path / "home"
    p = _rollout(home, "13", UUID_B, "dev-codex-g2", age_s=10)
    raw = p.read_bytes().split(b"\n")
    k = raw[0].index(b"xxxx") + 2                                    # INSIDE a string value (the instructions),
    raw[0] = raw[0][:k] + b"\xff\xfe" + raw[0][k:]                  # so replace-decoding would keep the JSON valid
    p.write_bytes(b"\n".join(raw))
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None
    p2 = _rollout(home, "12", UUID_A, "dev-codex-g2", age_s=3600)
    raw = p2.read_bytes().split(b"\n")
    bi = next(i for i, l in enumerate(raw) if b"You are dev-codex-g2" in l)
    k = raw[bi].index(b"a seat in") + 2                               # inside the boot text, after the name
    raw[bi] = raw[bi][:k] + b"\xc3" + raw[bi][k:]                     # a lone lead byte
    p2.write_bytes(b"\n".join(raw))
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None


def test_json_nested_past_the_parser_limit_never_raises_and_older_candidates_are_still_tried(ra, tmp_path):
    home = tmp_path / "home"
    p = _rollout(home, "13", UUID_B, "dev-codex-g2", age_s=10)
    lines = p.read_text().split("\n")
    deep = "[" * 200000 + "]" * 200000                   # valid JSON, deeper than the recursion limit
    lines = [lines[0], deep] + lines[1:]
    p.write_text("\n".join(lines))
    _rollout(home, "12", UUID_A, "dev-codex-g2", age_s=3600)
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A


def test_the_line_cap_gives_up_before_a_boot_line_that_sits_too_deep(ra, tmp_path):
    home = tmp_path / "home"
    p = _rollout(home, "13", UUID_B, "dev-codex-g2", age_s=10)
    lines = p.read_text().split("\n")
    filler = [json.dumps({"type": "event_msg", "payload": {"type": "token_count"}})] * 250
    p.write_text("\n".join([lines[0]] + filler + lines[1:]))
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None


def test_the_byte_cap_is_counted_before_decoding_and_an_overlong_line_gives_up(ra, tmp_path):
    home = tmp_path / "home"
    big = _umsg("z" * (600 * 1024))                   # one line past the 512 KB cap, BEFORE the boot line
    p = _rollout(home, "13", UUID_A, "dev-codex-g2", age_s=10)
    lines = p.read_text().split("\n")
    lines = lines[:1] + [json.dumps(big)] + lines[1:]
    p.write_text("\n".join(lines))
    assert ra.extract_active_sid("dev-codex-g2", "codex") is None
    # multibyte text BEFORE the boot line, written as real UTF-8 (not \\u escapes), inside the cap:
    # bytes are what is counted, and the boot line after it is still found
    p2 = _rollout(home, "12", UUID_B, "pm-codex-g5", age_s=20)
    lines = p2.read_text(encoding="utf-8").split("\n")
    row = json.dumps(_umsg("\u00e9\u4e2d" * 2000), ensure_ascii=False)
    assert "\u00e9" in row and "\\u00e9" not in row
    p2.write_text("\n".join([lines[0], row] + lines[1:]), encoding="utf-8")
    assert ra.extract_active_sid("pm-codex-g5", "codex") == UUID_B


def test_the_claude_and_gemini_branches_are_not_consulted_for_codex(ra, tmp_path, monkeypatch):
    """A codex lookup never reads Claude's projects dir."""
    called = []
    monkeypatch.setattr(ra, "_claude_projects_dir", lambda: called.append(1) or (tmp_path / "nope"))
    _rollout(tmp_path / "home", "13", UUID_A, "dev-codex-g2", age_s=60)
    assert ra.extract_active_sid("dev-codex-g2", "codex") == UUID_A
    assert called == []


def _spawn_boot_lines(seat):
    """The boot lines spawn-agent.sh really types into a Codex pane, read from the script itself
    (both inject_or_fail calls: first boot and roster resume), so the fixture cannot drift from
    what a seat receives. orchestraos-builder on #429 r5: the real line is "You are <seat>. Read
    ..." (a full stop), and every hand-written fixture used a comma."""
    script = (HERE.parent / "spawn-agent.sh").read_text()
    import re
    found = re.findall(r'inject_or_fail "\$tmux_name" "(You are \$agent_id[^"]*)"', script)
    assert len(found) == 2, found      # first boot + resume; a third or a rename must be looked at
    subs = {"$agent_id": seat, "$init_file": f"/tmp/agent-init-{seat}.md",
            "$resume_sid": UUID_A, "$SCRIPT_DIR": "/opt/orchestraos/scripts"}
    out = {}
    for line in found:
        for k, v in subs.items():
            line = line.replace(k, v)
        assert "$" not in line, line
        out["resume" if ", resumed (session" in line else "first-boot"] = line
    assert set(out) == {"first-boot", "resume"}, out
    return out


@pytest.mark.parametrize("which", ["first-boot", "resume"])
@pytest.mark.parametrize("seat", ["codex-gm", "codex-gm-g7", "codex-gm.ops"])
def test_the_boot_line_spawn_agent_really_types_is_found(ra, tmp_path, seat, which):
    home = tmp_path / "home"
    line = _spawn_boot_lines(seat)[which]
    _rollout(home, "13", UUID_B, seat, age_s=10, boot=False, extra_rows=[_umsg(line)])
    assert ra.extract_active_sid(seat, "codex") == UUID_B


def test_a_name_with_a_dot_inside_stays_distinct_on_the_real_boot_line(ra, tmp_path):
    home = tmp_path / "home"
    _rollout(home, "12", UUID_A, "codex-gm", age_s=3600, boot=False,
             extra_rows=[_umsg(_spawn_boot_lines("codex-gm")["first-boot"])])
    _rollout(home, "13", UUID_B, "codex-gm.ops", age_s=10, boot=False,
             extra_rows=[_umsg(_spawn_boot_lines("codex-gm.ops")["first-boot"])])
    assert ra.extract_active_sid("codex-gm", "codex") == UUID_A
    assert ra.extract_active_sid("codex-gm.ops", "codex") == UUID_B
