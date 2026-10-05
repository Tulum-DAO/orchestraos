"""Tests for the operator-identifier gate.

NOTHING HERE IS STUBBED. Every test writes a real file and runs the real scanner end to
end. That is deliberate: round 5 of #156 proved a suite can be unanimously green while the
function under it is broken, because all eight of its tests monkeypatched the one thing
that mattered. A gate whose tests stub the gate is not a gate.

Each rule gets a POSITIVE case (the leak is caught) and a NEGATIVE case (the synthetic or
placeholder form a contributor is supposed to write still passes). The negative cases are
the ones that keep this gate switched on.
"""
import json
import subprocess
import sys

import scan_operator_identifiers as S

SCANNER = "scripts/scan_operator_identifiers.py"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _run(paths, baseline=None):
    cmd = [sys.executable, SCANNER] + list(paths)
    if baseline:
        cmd += ["--baseline", baseline]
    return subprocess.run(cmd, capture_output=True, text=True)


# --------------------------------------------------------------- P1 session id

def test_a_real_looking_uuid_is_caught(tmp_path):
    f = _write(tmp_path, "t.py", 'sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    r = _run([f])
    assert r.returncode == 1
    assert "session-id" in r.stderr and "6da0611c" in r.stderr


def test_the_three_real_sids_published_in_this_repo_are_all_caught(tmp_path):
    """The exact values this gate was built in response to.

    If a future refactor loosens the threshold, this is the test that reddens.
    """
    for sid in ("6da0611c-3ad3-4b99-a385-a681a174a628",  # operator-id-ok: synthetic test input, resolves to nothing
                "442d2cf1-9809-49d9-a431-e4f7c6914480",  # operator-id-ok: synthetic test input, resolves to nothing
                "7368f3db-f997-4968-a18c-6f791899162c"):  # operator-id-ok: synthetic test input, resolves to nothing
        f = _write(tmp_path, f"{sid[:8]}.txt", f"claude --resume {sid}\n")
        assert _run([f]).returncode == 1, sid


def test_a_visibly_synthetic_uuid_passes(tmp_path):
    """The form a test SHOULD use. If this reddens, the gate is unusable."""
    for sid in ("deadbeef-0000-0000-0000-000000000000",
                "11111111-1111-1111-1111-111111111111",
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "44444444-4444-4444-8444-444444444444",
                "aaaa1111-bbbb-2222-cccc-333344445555"):
        f = _write(tmp_path, f"{sid[:8]}.py", f'sid = "{sid}"\n')
        assert _run([f]).returncode == 0, sid


def test_the_threshold_sits_in_the_measured_gap():
    """<=8 distinct hex is synthetic, >=13 is random-looking; nothing sat in 9..12."""
    assert S.is_synthetic_uuid("aaaa1111-bbbb-2222-cccc-333344445555")      # 8 distinct
    assert not S.is_synthetic_uuid("6da0611c-3ad3-4b99-a385-a681a174a628")  # 13 distinct  # operator-id-ok: synthetic test input, resolves to nothing
    assert 8 < S.UUID_DISTINCT_HEX_THRESHOLD < 13


def test_an_uppercase_uuid_is_not_a_bypass(tmp_path):
    f = _write(tmp_path, "t.py", 'sid = "6DA0611C-3AD3-4B99-A385-A681A174A628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    assert _run([f]).returncode == 1


# ----------------------------------------------------------- P2 sid prefix

def test_a_sid_prefix_in_an_identifier_context_is_caught(tmp_path):
    f = _write(tmp_path, "t.md", "recovered with `claude --resume 6da0611c`\n")  # operator-id-ok: synthetic test input, resolves to nothing
    r = _run([f])
    assert r.returncode == 1
    assert "session-id-prefix" in r.stderr


def test_a_bare_abbreviated_git_sha_is_NOT_flagged(tmp_path):
    """P2 is context-gated precisely so this passes.

    An ungated 8-hex rule fires on every short sha in the repo. A gate that cries wolf
    gets switched off, and a switched-off gate catches nothing.
    """
    f = _write(tmp_path, "t.md", "main is at 189f366 and before that 3ac9c38 "
                                 "and the squash was c08349b, cc345d7, 5300131f\n")
    assert _run([f]).returncode == 0


def test_a_placeholder_sid_passes(tmp_path):
    f = _write(tmp_path, "t.md", "recovery was `claude --resume <sid>`\n")
    assert _run([f]).returncode == 0


# ----------------------------------------------- P3/P4 incident id + capture name

def test_an_incident_id_is_caught(tmp_path):
    f = _write(tmp_path, "t.md", "state/red-alert/20200101T000000Z-gm-process-suspended.json\n")  # operator-id-ok: synthetic test input, resolves to nothing
    r = _run([f])
    assert r.returncode == 1
    assert "incident-id" in r.stderr


def test_a_placeholder_incident_path_passes(tmp_path):
    f = _write(tmp_path, "t.md", "`state/red-alert/<incident id>-gm-process-suspended.json`\n")
    assert _run([f]).returncode == 0


def test_a_sentinel_control_file_is_not_a_capture(tmp_path):
    """`state/red-alert/DISARM` names a constant, not an incident."""
    f = _write(tmp_path, "t.py", "DISARM = 'state/red-alert/DISARM'\n")
    assert _run([f]).returncode == 0


# ------------------------------------------- P6/P7 locale, host, home path

def test_city_plus_local_clock_is_caught(tmp_path):
    # The operator's literal locale IS the pattern, so the input must be the real shape.
    # The pragma is what keeps this file clean while still exercising the rule.
    f = _write(tmp_path, "t.md", "the green died at 00:20 Tulum 2026-09-18\n")  # operator-id-ok: test input for the rule under test
    r = _run([f])
    assert r.returncode == 1 and "city-clock" in r.stderr


def test_a_utc_timestamp_without_a_city_passes(tmp_path):
    f = _write(tmp_path, "t.md", "the green died at 04:53Z\n")
    assert _run([f]).returncode == 0


def test_operator_home_path_and_host_are_caught(tmp_path):
    f = _write(tmp_path, "t.md", "/home/shaw/repos/x and srv1397016\n")  # operator-id-ok: test input for the rule under test
    r = _run([f])
    assert r.returncode == 1
    assert "home-path" in r.stderr and "host-or-email" in r.stderr


# ---------------------------------------- transcript timestamp (the #156 third leak)

def test_a_verbatim_transcript_millisecond_timestamp_is_caught(tmp_path):
    f = _write(tmp_path, "t.py", 'ts = "2020-01-01T00:00:00.123Z"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    r = _run([f])
    assert r.returncode == 1 and "transcript-timestamp" in r.stderr


def test_a_round_millisecond_fixture_timestamp_passes(tmp_path):
    """`.000` is evidence a human typed it. Flagging it was 44 alarms for 2 real hits."""
    f = _write(tmp_path, "t.json", '{"timestamp": "2026-08-27T10:00:00.000Z"}\n')
    assert _run([f]).returncode == 0


# --------------------------------------------------------------- FAIL-SAFE

def test_an_undecodable_file_is_REPORTED_not_skipped(tmp_path):
    """The #156 fail-open class. A gate an encoding defeats is not a gate."""
    p = tmp_path / "bad.py"
    p.write_bytes(b'sid = "\xff\xfe not utf-8"\n')
    r = _run([str(p)])
    assert r.returncode == 1
    assert "undecodable" in r.stderr


def test_scanning_no_files_is_an_ERROR_not_a_pass():
    """"Found nothing" must never be a misconfigured invocation wearing a green tick."""
    r = _run([])
    assert r.returncode == 2
    assert "NO FILES GIVEN" in r.stderr


def test_a_binary_file_is_skipped_but_COUNTED(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    r = _run([str(p)])
    assert r.returncode == 0
    assert "skipped 1 binary" in r.stdout


def test_coverage_is_always_printed(tmp_path):
    f = _write(tmp_path, "ok.py", "x = 1\n")
    r = _run([f])
    assert "scanned 1 files" in r.stdout


# --------------------------------------------------------------- BASELINE

def test_a_baselined_occurrence_passes_and_a_changed_value_does_not(tmp_path):
    """Value-keyed, not line-keyed: moving a line must not churn; changing it must fail."""
    f = _write(tmp_path, "t.py", 'sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    bl = str(tmp_path / "bl.json")
    key = f"{f}::session-id::6da0611c-3ad3-4b99-a385-a681a174a628"  # operator-id-ok: synthetic test input, resolves to nothing
    (tmp_path / "bl.json").write_text(json.dumps(
        {"accepted": [{"key": key, "reason": "pre-existing"}]}))
    assert _run([f], baseline=bl).returncode == 0

    # same file, line moved down -> still accepted
    (tmp_path / "t.py").write_text('# a new first line\nsid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    assert _run([f], baseline=bl).returncode == 0

    # value changed -> NOT accepted, because it is a different identifier
    (tmp_path / "t.py").write_text('sid = "442d2cf1-9809-49d9-a431-e4f7c6914480"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    assert _run([f], baseline=bl).returncode == 1


def test_a_baseline_does_not_launder_the_same_value_in_another_file(tmp_path):
    """Keyed by file AND value, so an accepted value is not globally whitelisted."""
    a = _write(tmp_path, "a.py", 'sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    b = _write(tmp_path, "b.py", 'sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    bl = str(tmp_path / "bl.json")
    (tmp_path / "bl.json").write_text(json.dumps(
        {"accepted": [{"key": f"{a}::session-id::6da0611c-3ad3-4b99-a385-a681a174a628",  # operator-id-ok: synthetic test input, resolves to nothing
                       "reason": "pre-existing"}]}))
    assert _run([a], baseline=bl).returncode == 0
    assert _run([b], baseline=bl).returncode == 1


def test_a_missing_baseline_file_does_not_silently_accept_everything(tmp_path):
    f = _write(tmp_path, "t.py", 'sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    assert _run([f], baseline=str(tmp_path / "nope.json")).returncode == 1


# ------------------------- gaps named by the round-1 reviewers

def test_a_compound_sid_identifier_is_also_a_context(tmp_path):
    """`green_sid`/`NEW_SID`/`sessionID` are the same context as `sid`.

    An earlier `\\bsid\\b` gate missed every compound name, which is most of the real
    ones in this repo.
    """
    for decl in ('green_sid = "6da0611c"', 'NEW_SID = "6da0611c"',  # operator-id-ok: synthetic test input, resolves to nothing
                 'operator_sid: "6da0611c"', 'sessionID = "6da0611c"',  # operator-id-ok: synthetic test input, resolves to nothing
                 'blue_session_id = "6da0611c"'):  # operator-id-ok: synthetic test input, resolves to nothing
        f = _write(tmp_path, "t.py", decl + "\n")
        assert _run([f]).returncode == 1, decl


def test_a_stale_baseline_entry_is_reported(tmp_path):
    """A baseline must shrink. An entry matching nothing is a permission nobody reviewed."""
    f = _write(tmp_path, "t.py", "x = 1\n")
    bl = str(tmp_path / "bl.json")
    (tmp_path / "bl.json").write_text(json.dumps(
        {"accepted": [{"key": "gone.py::session-id::6da0611c-3ad3-4b99-a385-a681a174a628",  # operator-id-ok: synthetic test input, resolves to nothing
                       "reason": "pre-existing"}]}))
    r = _run([f], baseline=bl)
    assert r.returncode == 0
    assert "stale baseline" in r.stdout.lower()
    assert "gone.py" in r.stdout


def test_the_repo_itself_is_clean_under_its_own_baseline():
    """The gate must pass on the tree it ships with, or CI is red on arrival."""
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    r = _run(files, baseline="scripts/operator_identifiers_baseline.json")
    assert r.returncode == 0, r.stderr[-4000:]


# ------------------- gaps named by the round-2 reviewer (all verified real)

def test_a_dashless_32_hex_session_id_is_caught_with_NO_keyword_context(tmp_path):
    """detect-secrets already reports this form, so omitting it would make this gate
    weaker than the one it supplements.

    Deliberately written with NO session-id keyword on the line. An earlier version of
    this test said `sid = "..."`, which the context-gated prefix rule caught -- so the
    test passed while the dashless rule it was named after did nothing. Mutation testing
    found that: deleting the dashless rule failed zero tests.
    """
    f = _write(tmp_path, "t.md", "see 6da0611c3ad34b99a385a681a174a628 for the capture\n")  # operator-id-ok: synthetic test input, resolves to nothing
    assert _run([f]).returncode == 1


def test_a_provider_correlation_id_is_caught(tmp_path):
    """The class found live on main in a fixture whose session_id HAD been scrubbed:
    the scrubber replaced the fields it recognised and left msg_/req_ behind."""
    f = _write(tmp_path, "t.jsonl",
               '{"id": "msg_EXAMPLEfixtureIDnotreal01", '  # pragma: allowlist secret  operator-id-ok: synthetic test input, resolves to nothing
               '"request_id": "req_EXAMPLEfixtureIDnotreal02"}\n')  # operator-id-ok: synthetic test input, resolves to nothing
    r = _run([f])
    assert r.returncode == 1
    assert "provider-id" in r.stderr


def test_a_synthetic_provider_id_passes(tmp_path):
    f = _write(tmp_path, "t.jsonl", '{"id": "msg_00000000000000000000000000"}\n')
    assert _run([f]).returncode == 0


def test_a_bare_hex_run_near_a_sid_keyword_is_caught(tmp_path):
    """A line-scoped gate missed `# green, not blue 6da0611c` -- a real sid in a comment  # operator-id-ok: documents the shape this gate matches
    two lines from the keyword."""
    f = _write(tmp_path, "t.py",
               'def test_swap(canonical_sid):\n'
               '    pass\n'
               '# green, not blue 6da0611c\n')  # operator-id-ok: synthetic test input, resolves to nothing
    assert _run([f]).returncode == 1


def test_an_all_decimal_run_near_a_sid_keyword_is_NOT_caught(tmp_path):
    """The window rule is weaker evidence, so it demands a hex letter: an epoch or row id
    beside the word `sid` is not a session id, and flagging it is switch-it-off noise."""
    f = _write(tmp_path, "t.py", 'sid_row = 1789507884583046\n')
    assert _run([f]).returncode == 0


def test_a_utf16_file_containing_a_session_id_is_not_missed(tmp_path):
    """The hard case is not an undecodable file -- it is one that decodes CLEANLY under
    the wrong codec, where a naive scanner reads mojibake and reports nothing."""
    p = tmp_path / "u16.txt"
    p.write_bytes('sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n'.encode("utf-16-le"))  # operator-id-ok: synthetic test input, resolves to nothing
    r = _run([str(p)])
    assert r.returncode == 1, r.stdout + r.stderr


def test_a_path_that_looks_like_a_flag_is_not_eaten(tmp_path):
    """`$(git ls-files)` lets a `-`-prefixed path be parsed as an option. --git-ls is the
    fix; this proves the failure mode is real and that an odd name still scans."""
    p = tmp_path / "-weird name.py"
    p.write_text('sid = "6da0611c-3ad3-4b99-a385-a681a174a628"\n')  # operator-id-ok: synthetic test input, resolves to nothing
    r = subprocess.run([sys.executable, SCANNER, "--", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 1


def test_coverage_is_ENFORCED_not_merely_printed(tmp_path):
    """#156's secret-scan stayed green while leaking. Printed coverage is not read."""
    f = _write(tmp_path, "ok.py", "x = 1\n")
    r = _run([f] + ["--min-files", "500"])
    assert r.returncode == 2
    assert "below the committed floor" in r.stderr


def test_the_baseline_contains_no_value_that_resolves_to_a_real_session():
    """Stored as sha256, never verbatim: a test that lists the real session ids would
    republish exactly what this gate exists to forbid.

    These are the digests of session ids proven real while building this gate. If any
    reappears in the baseline, someone has baselined a live operator identifier.
    """
    import hashlib
    forbidden = {
        "0bd1a0d91b1d0b86d7e0a2ba78bbbe6e8c4ebc6a4a31e2de6eec0fd1e8dd4a67",  # pragma: allowlist secret
    }
    d = json.load(open("scripts/operator_identifiers_baseline.json"))
    for entry in d["accepted"]:
        value = entry["key"].rsplit("::", 1)[-1]
        assert hashlib.sha256(value.encode()).hexdigest() not in forbidden, entry["key"]


def test_every_baseline_entry_carries_a_reason():
    """A baseline without reasons is a permission list nobody reviewed."""
    d = json.load(open("scripts/operator_identifiers_baseline.json"))
    assert d["accepted"], "baseline should not be empty while the worklist stands"
    for entry in d["accepted"]:
        assert entry.get("reason", "").strip(), entry["key"]


def test_the_full_tree_scan_is_DETERMINISTIC():
    """Two identical runs must produce identical output, byte for byte.

    This exists because of an observation I could not explain: during development a
    full-tree run twice reported 54 hits / 17 NEW -- seventeen `.000Z` timestamps that the
    rule explicitly exempts -- and then reported 37 / 0 on the next six runs and has
    never done it again. The scanner source was byte-identical throughout (checked
    against a backup), and the regex returns no match for a `.000Z` value in isolation,
    so I have no mechanism to offer.

    An intermittently-failing gate is worse than no gate: it blocks unrelated PRs at
    random and teaches people to re-run CI until it passes, which is how a real finding
    gets clicked past. So rather than assert the thing I cannot reproduce is gone, this
    test fails the build if the scan is ever not reproducible.
    """
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    a = _run(files, baseline="scripts/operator_identifiers_baseline.json")
    b = _run(files, baseline="scripts/operator_identifiers_baseline.json")
    assert a.returncode == b.returncode, (a.returncode, b.returncode)
    assert a.stdout == b.stdout, "scan output differs between identical runs"
    assert a.stderr == b.stderr, "scan findings differ between identical runs"
