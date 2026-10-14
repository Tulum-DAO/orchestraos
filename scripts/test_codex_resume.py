"""spawn-agent.sh --resume <sid> for a Codex seat: pinned, never a picker.

`codex resume` with no session id opens an interactive picker (codex-cli 0.153.4 `codex resume
--help`: "picker by default; use --last to continue the most recent"), so a resume must always carry
the exact UUID. spawn-agent.sh refused `--resume` for codex outright (exit 3, "claude/grok-only").
This adds the codex branch, mirroring build_grok_launch_cmd: a pure function that is sourced and
tested here, used by the codex launch line.
"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SID = "00000000-0000-7000-8000-00000000000c"


def _bash(script, tmp_path, registry=None):
    tree = tmp_path / "tree"
    (tree / "state").mkdir(parents=True, exist_ok=True)
    reg = tree / "registry.json"
    reg.write_text(json.dumps(registry or {"agents": {}}))
    env = dict(os.environ, ORCHESTRA_DIR=str(tree), REGISTRY=str(reg), NTFY_TOKEN_FILE="/nonexistent")
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    # $0 is not the script path, so spawn-agent.sh's main guard stays off when sourced.
    return subprocess.run(["bash", "-c", f'source "$1"; {script}', "spawn-test", str(ROOT / "spawn-agent.sh")],
                          capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)


def test_a_uuid_gives_a_pinned_resume_argument(tmp_path):
    r = _bash(f"build_codex_resume_args {SID}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"resume {SID}"


def test_anything_but_a_uuid_is_refused_so_the_picker_can_never_open(tmp_path):
    for bad in ("", "my-title", "--last", "rollout-2026-10-13T08-00-00-" + SID, SID + "x"):
        r = _bash(f"build_codex_resume_args '{bad}' || echo rc=$?", tmp_path)
        assert r.stdout.strip() == "rc=2", (bad, r.stdout, r.stderr)


def test_the_codex_tail_ends_in_resume_sid_and_a_fresh_seat_has_no_tail(tmp_path):
    """The branch APPENDS this tail to the launch line spawn_agent already built (bg marker, data-dir
    prefix, installer-session scrub, binary, bypass flags), so a resume line ends in `resume <uuid>`
    and every prefix survives (orchestraos-builder, #430 r4: an assignment dropped them)."""
    r = _bash(f"build_codex_launch_tail gpt-5.6-terra {SID}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"--model gpt-5.6-terra resume {SID}"
    assert _bash(f"build_codex_launch_tail '' {SID}", tmp_path).stdout.strip() == f"resume {SID}"
    assert _bash("build_codex_launch_tail gpt-5.6-terra ''", tmp_path).stdout.strip() == "--model gpt-5.6-terra"
    assert _bash("build_codex_launch_tail '' ''", tmp_path).stdout.strip() == ""
    r = _bash("build_codex_launch_tail '' my-title || echo rc=$?", tmp_path)
    assert r.stdout.strip() == "rc=2"


def test_the_codex_branch_appends_the_tail_and_never_assigns_launch_cmd():
    """Static guard: the branch must only ever `launch_cmd+=`; any assignment rebuilds the line and
    drops the prefixes. (A by-effect test through spawn_agent with stub tmux is still owed.)"""
    src = (ROOT / "spawn-agent.sh").read_text()
    codex_branch = src[src.index('elif [[ "$runtime" == "codex" ]]; then'):]
    codex_branch = codex_branch[:codex_branch.index('elif [[ "$runtime" == "grok" ]]; then')]
    assert 'launch_cmd+=" $_codex_tail"' in codex_branch
    assert 'launch_cmd="' not in codex_branch
    assert "build_codex_launch_cmd" not in codex_branch


def test_resume_is_no_longer_refused_for_codex():
    src = (ROOT / "spawn-agent.sh").read_text()
    assert "--resume is claude/grok-only" not in src
    i = src.index('if [[ -n "$resume_sid" && "$runtime" != "claude"')
    cond = src[i:src.index("]]", i)]
    assert '"$runtime" != "codex"' in cond
    assert '"$runtime" != "grok"' in cond          # grok's own entry untouched (#422)


def test_both_resume_string_emitters_refuse_a_non_uuid_for_codex():
    """gm (msg_005df672): the UUID rule holds in BOTH emitters of the codex resume string, not only
    in spawn: promote_successor (the swap path) and spawn_attribute_sid (the spawn record)."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "scripts" / "identity_store"))
    import promote_successor as ps
    import spawn_attribute_sid as sas
    assert ps.resume_command_for("codex", SID) == f"codex --yolo resume {SID}"
    assert sas.resume_command_for("codex", SID) == f"codex --yolo resume {SID}"
    for bad in ("s1", "my title", "--last", "", SID + "x", SID + "\n"):   # $ would accept the newline; fullmatch does not
        try:
            ps.resume_command_for("codex", bad)
        except ps.RuntimeRefused:
            pass
        else:
            assert bad == "", bad          # the empty sid is refused earlier, by the generic check
        assert sas.resume_command_for("codex", bad) is None, bad
