"""RED-first: post-spawn model verification can never abort the spawn (B1 run-2 finding A).

spawn-agent.sh runs under `set -euo pipefail`. Reading the model with an unguarded
`line=$(... | grep ... | tail -1)` aborted the whole spawn (exit 1, silent) whenever the
banner had no `claude-<family>` token — Claude Code >= 2.1.26x banners as "Fable 5.1 with
medium effort", so every from-docs spawn died after launch and before --task injection.
The helpers live in scripts/spawn_model_verify.sh and speak the banner's own vocabulary.
"""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "spawn_model_verify.sh")


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f"set -euo pipefail; source '{HELPER}'; {script}"],
                          capture_output=True, text=True, timeout=30)


def test_read_model_line_does_not_abort_under_set_e_when_nothing_matches():
    r = _bash('line=$(read_model_line "no model here at all"); echo "after:[$line]"')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "after:[]"


def test_classify_banner_label_is_family_not_bare():
    r = _bash('classify_model_line "  ▝▝ ▝▝    Fable 5.1 with medium effort · Claude Max"')
    assert r.stdout == "family", r.stdout


def test_classify_explicit_1m_token():
    r = _bash('classify_model_line "│ claude-opus-4-8[1m] │ agent-orchestra"')
    assert r.stdout == "1m"


def test_classify_bare_model_id_is_the_only_correctable_case():
    r = _bash('classify_model_line "│ claude-opus-4-8 │ agent-orchestra"')
    assert r.stdout == "bare"


def test_classify_empty_is_none():
    r = _bash('classify_model_line ""')
    assert r.stdout == "none"


def test_read_model_line_finds_label_line_in_a_pane_dump():
    pane = "line one\\n ▐▛███▛█   Claude Code v2.1.260\\n▝▜██████▀  Fable 5.1 with medium effort · Claude Max\\n❯ "
    r = _bash(f'line=$(read_model_line "$(printf "{pane}")"); classify_model_line "$line"')
    assert r.stdout == "family", (r.stdout, r.stderr)
