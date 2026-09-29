# RED-first tests for per-turn provider/model routing (DEC-1790669162399904, spec v4 §1.2–1.3):
# the attached-model argv form, the catalog allow-list, the gemini catalog fix, strict-brain
# failure signalling, and the BrainPool that builds one brain per (provider, model).
import json
import re
from pathlib import Path

import pytest

from services.arturo import brain as B

REPO = Path(__file__).resolve().parents[2]
PROVIDERS = REPO / "config" / "providers.json"
HOSTILE = ["--dangerously-skip-permissions", "--version", "-x", "a b", "claude;rm -rf /", "", "x" * 101]


# ---- argv: the model is attached, never a separate element --------------------------------

@pytest.mark.parametrize("runtime,cli", [("claude", "claude"), ("gemini", "agy"), ("codex", "codex")])
def test_model_rides_attached_on_every_runtime(runtime, cli, tmp_path):
    argv = B.runtime_command(runtime, cli, "S", "P", model="some-model", scratch=tmp_path).argv
    assert "--model=some-model" in argv
    assert "--model" not in argv and "-m" not in argv


@pytest.mark.parametrize("runtime,cli", [("claude", "claude"), ("gemini", "agy"), ("codex", "codex")])
def test_hostile_model_value_never_becomes_its_own_argv_element(runtime, cli, tmp_path):
    # agy 1.2.13 re-reads a hyphen-leading --model value as a flag (verified: --model --version
    # printed the version). Attached, it is one element that no parser can split off.
    argv = B.runtime_command(runtime, cli, "S", "P", model="--version", scratch=tmp_path).argv
    assert "--version" not in argv
    assert "--model=--version" in argv


# ---- the catalog allow-list ---------------------------------------------------------------

def test_load_model_catalog_reads_model_catalog_static():
    cat = B.load_model_catalog(PROVIDERS)
    assert set(cat) >= {"claude", "gemini", "codex"}
    raw = json.loads(PROVIDERS.read_text())
    for p in raw["providers"]:
        assert cat[p["id"]] == [m["id"] for m in p["model_catalog"]["static"]]


def test_validate_accepts_catalog_ids_and_empty_default():
    cat = {"claude": ["claude-sonnet-5"], "gemini": ["gemini-3.7-flash-high"], "codex": []}
    B.validate_model("claude", "claude-sonnet-5", cat)
    B.validate_model("gemini", "gemini-3.7-flash-high", cat)
    B.validate_model("codex", "", cat)          # empty = the CLI's own default


def test_validate_rejects_ids_not_in_the_catalog():
    cat = {"claude": ["claude-sonnet-5"]}
    with pytest.raises(B.ModelNotAllowed):
        B.validate_model("claude", "claude-sonnet-4", cat)


@pytest.mark.parametrize("bad", [h for h in HOSTILE if h])
def test_validate_rejects_hostile_values_even_if_someone_puts_them_in_the_catalog(bad):
    # belt and braces: the shape rule holds even against a poisoned catalog
    with pytest.raises(B.ModelNotAllowed):
        B.validate_model("claude", bad, {"claude": [bad]})


def test_validate_rejects_unknown_provider():
    with pytest.raises(B.ModelNotAllowed):
        B.validate_model("grok", "", {"claude": []})


def test_validate_api_provider_takes_only_the_empty_model_in_v1():
    B.validate_model("api", "", {})
    with pytest.raises(B.ModelNotAllowed):
        B.validate_model("api", "gemini-2.5-pro", {})


# ---- the shipped catalog is usable by the installed CLIs -----------------------------------

def test_every_shipped_catalog_id_passes_validation_and_rides_attached(tmp_path):
    cat = B.load_model_catalog(PROVIDERS)
    clis = {p["id"]: p["cli"] for p in json.loads(PROVIDERS.read_text())["providers"]}
    for provider, ids in cat.items():
        for mid in ids:
            B.validate_model(provider, mid, cat)
            argv = B.runtime_command(provider, clis[provider], "S", "P", model=mid, scratch=tmp_path).argv
            assert argv.count(f"--model={mid}") == 1, (provider, mid)


def test_gemini_catalog_ids_carry_the_effort_suffix_agy_requires():
    # agy 1.2.13: `--model=gemini-3.7-flash` -> "requires --effort (available: low, medium, high)";
    # `agy models` lists only effort-suffixed ids. A bare id = every explicit gemini pick 502s.
    for mid in B.load_model_catalog(PROVIDERS)["gemini"]:
        assert re.search(r"-(low|medium|high)$", mid), mid


# ---- strict brains signal failure instead of letting prose pass as an answer ---------------

def _boom(spec, timeout):
    raise RuntimeError("cli exploded")


def test_strict_runtime_brain_sets_turn_failure_on_cli_failure():
    tok = B.TURN_FAILURE.set(None)
    try:
        b = B.RuntimeBrain("claude", "claude", model="", runner=_boom, strict=True)
        r = b.complete([{"role": "user", "content": "hi"}])
        assert r.choices[0].message.content          # still answers in prose (callers unchanged)
        f = B.TURN_FAILURE.get()
        assert f and f["code"] == "brain_failed" and f["provider"] == "claude"
        assert "cli exploded" not in json.dumps(f)   # detail stays in the log, as today
    finally:
        B.TURN_FAILURE.reset(tok)


def test_default_runtime_brain_never_touches_turn_failure():
    tok = B.TURN_FAILURE.set(None)
    try:
        B.RuntimeBrain("claude", "claude", model="", runner=_boom).complete([{"role": "user", "content": "hi"}])
        assert B.TURN_FAILURE.get() is None
    finally:
        B.TURN_FAILURE.reset(tok)


def test_strict_brain_success_leaves_turn_failure_unset():
    tok = B.TURN_FAILURE.set(None)
    try:
        B.RuntimeBrain("claude", "claude", model="", runner=lambda s, t: "fine", strict=True).complete(
            [{"role": "user", "content": "hi"}])
        assert B.TURN_FAILURE.get() is None
    finally:
        B.TURN_FAILURE.reset(tok)


# ---- BrainPool ------------------------------------------------------------------------------

def _probes(*authed_ids):
    table = {"claude": "claude", "gemini": "agy", "codex": "codex"}
    return [{"id": i, "cli": c, "installed": True, "authed": i in authed_ids} for i, c in table.items()]


def test_pool_builds_a_strict_runtime_brain_for_the_requested_provider_and_model():
    pool = B.BrainPool(probes=lambda: _probes("codex"), api_key="")
    b = pool.get("codex", "gpt-5.6-terra")
    assert b.kind == "runtime" and b.runtime == "codex" and b.cli == "codex"
    assert b.model == "gpt-5.6-terra" and b.strict is True


def test_pool_reuses_the_instance_for_the_same_key_and_splits_by_model():
    pool = B.BrainPool(probes=lambda: _probes("claude"), api_key="")
    a = pool.get("claude", "claude-sonnet-5")
    assert pool.get("claude", "claude-sonnet-5") is a
    assert pool.get("claude", "claude-haiku-4-5-20251001") is not a


def test_pool_refuses_a_provider_that_is_not_authed_never_falls_back():
    pool = B.BrainPool(probes=lambda: _probes("claude"), api_key="")
    with pytest.raises(B.ProviderUnavailable) as e:
        pool.get("codex", "")
    assert e.value.provider == "codex" and e.value.reason


def test_pool_api_provider_needs_the_key():
    with pytest.raises(B.ProviderUnavailable):
        B.BrainPool(probes=lambda: [], api_key="").get("api", "")
    built = {}
    pool = B.BrainPool(probes=lambda: [], api_key="k", api_model="gemini-2.5-flash",
                       api_factory=lambda key, model: built.setdefault("b", B.NullBrain(f"{key}:{model}")))
    assert pool.get("api", "") is built["b"] and built["b"].reason == "k:gemini-2.5-flash"


# ---- a CLI that exits non-zero has FAILED, even when it printed to stdout -------------------
# Found live 2026-09-29: `claude -p` with an expired OAuth session exits 1 and prints
# "Failed to authenticate: OAuth session expired and could not be refreshed" on STDOUT (stderr
# empty). run_command only raised when stdout was EMPTY, so the error came back as an answer
# and was saved into the operator's thread.

AUTH_FAIL = ["sh", "-c", "echo 'Failed to authenticate: OAuth session expired and could not be refreshed'; exit 1"]


def test_nonzero_exit_with_stdout_raises_and_is_classified_as_auth():
    with pytest.raises(B.CliFailed) as e:
        B.run_command(B.CommandSpec(argv=AUTH_FAIL), timeout=10)
    assert e.value.auth is True and e.value.returncode == 1


def test_nonzero_exit_that_is_not_about_login_is_a_plain_failure():
    with pytest.raises(B.CliFailed) as e:
        B.run_command(B.CommandSpec(argv=["sh", "-c", "echo 'rate limited, try later'; exit 2"]), timeout=10)
    assert e.value.auth is False and e.value.returncode == 2


def test_a_zero_exit_still_returns_its_stdout():
    assert B.run_command(B.CommandSpec(argv=["sh", "-c", "echo PONG"]), timeout=10).strip() == "PONG"


def _auth_runner(spec, timeout):
    return B.run_command(B.CommandSpec(argv=AUTH_FAIL), timeout)


def test_default_brain_says_it_is_logged_out_instead_of_echoing_the_cli():
    r = B.RuntimeBrain("claude", "claude", model="", runner=_auth_runner).complete([{"role": "user", "content": "hi"}])
    text = r.choices[0].message.content
    assert "logged in" in text and "claude" in text
    assert "OAuth session expired" not in text          # the raw CLI line stays in the log


def test_strict_brain_reports_not_logged_in():
    tok = B.TURN_FAILURE.set(None)
    try:
        B.RuntimeBrain("codex", "codex", model="", runner=_auth_runner, strict=True).complete(
            [{"role": "user", "content": "hi"}])
        f = B.TURN_FAILURE.get()
        assert f["code"] == "brain_failed" and f["reason"] == "not_logged_in" and f["provider"] == "codex"
    finally:
        B.TURN_FAILURE.reset(tok)


def test_claude_catalog_offers_opus_5_5():
    # 2026-09-29: the claude CLI's own default is Opus 5.5 and it accepts --model=claude-opus-5-5,
    # but the static catalog stopped at Opus 5, so the operator could not pick it explicitly.
    assert "claude-opus-5-5" in B.load_model_catalog(PROVIDERS)["claude"]
