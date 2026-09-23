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


def test_launch_prefix_carries_install_env_into_the_pane(tmp_path):
    """Tier 0 item 2: the pane must see ORCHESTRA_DIR / ORCHESTRA_ROOT (and CLAUDE_CONFIG_DIR when
    set) — tmux new-session inherits the SERVER env, so spawn-agent.sh prefixes the launch."""
    src = open(os.path.join(os.path.dirname(HERE), "spawn-agent.sh")).read()
    assert "ORCHESTRA_DIR=%q ORCH_DIR=%q ORCHESTRA_ROOT=%q" in src
    assert "CLAUDE_CONFIG_DIR=%q" in src


# ---- #95: the guard must not fire on a default install ------------------------------------
# On a default install the configured model is a plain id (config/providers.json ships NO [1m]
# SKU for any family), so classify returns `bare` and verify_spawn_model "corrects" toward a
# variant that does not exist. Both lines from the issue then print on EVERY spawn, forever:
#   '<agent>' came up on a BARE (non-[1m]) model — correcting
#   still not [1m] after /model claude-sonnet-5 — flag for the operator
# A correction is only meaningful when the operator ASKED for a [1m] model.

def test_correction_is_only_warranted_when_the_intended_model_is_1m():
    for intended, want in (("claude-sonnet-5", "no"), ("", "no"),
                           ("claude-opus-4-8[1m]", "yes"), ("claude-opus-4-8", "no")):
        r = _bash(f'correction_warranted "{intended}" && echo yes || echo no')
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == want, f"{intended!r} -> {r.stdout.strip()} (want {want})"
