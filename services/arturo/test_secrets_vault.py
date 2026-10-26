"""Keys Arturo uses but never sees or says."""
from services.arturo.secrets_vault import Vault
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture

FAKE = "nlfy_0000000000000000TESTVALUE"


def _vault(tmp_path, extra=""):
    f = tmp_path / ".env.secrets"
    f.write_text(f"# keys\nNETLIFY_TOKEN={FAKE}\nexport QUOTED='quoted-value-1234'\nPORT=80\n{extra}")
    return Vault(f, environ={"PATH": "/usr/bin", "OTHER_API_KEY": "env-held-value-99"})


def test_commands_get_the_keys_by_name(tmp_path):
    v = _vault(tmp_path)
    env = v.env()
    assert env["NETLIFY_TOKEN"] == FAKE and env["QUOTED"] == "quoted-value-1234" and env["PATH"] == "/usr/bin"
    assert v.names() == ["NETLIFY_TOKEN", "PORT", "QUOTED"]


def test_every_value_is_blanked_to_its_name(tmp_path):
    v = _vault(tmp_path)
    out = v.scrub(f"NETLIFY_TOKEN={FAKE}\nQUOTED=quoted-value-1234\nother env-held-value-99\nPORT=80")
    assert FAKE not in out and "quoted-value-1234" not in out and "env-held-value-99" not in out
    assert "[NETLIFY_TOKEN]" in out and "PORT=80" in out                     # a short value is not a key


def test_key_shaped_strings_not_on_file_are_blanked_too(tmp_path):
    out = _vault(tmp_path).scrub("token sk-proj-" + "a" * 30 + " and ghp_" + "b" * 30)
    assert out == "token [key] and [key]"


def test_a_changed_file_is_read_again(tmp_path):
    v = _vault(tmp_path)
    assert v.scrub("new-secret-value-77") == "new-secret-value-77"
    import os
    import time
    f = tmp_path / ".env.secrets"
    f.write_text("NEW_KEY=new-secret-value-77\n")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert v.scrub("new-secret-value-77") == "[NEW_KEY]"


def test_no_tool_result_carries_a_key(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "_VAULT", _vault(tmp_path))
    monkeypatch.setattr(P, "_execute_tool_raw", lambda name, args, user_turns=None: f"output: {FAKE}")
    assert P._execute_tool_impl("run_command", {"command": "x"}) == "output: [NETLIFY_TOKEN]"


def test_run_command_runs_with_bash_and_the_keys(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "_VAULT", _vault(tmp_path))
    from services.arturo.test_jobs import _as_dashboard
    done = _as_dashboard(P, "web_keys")
    try:
        out = P._execute_tool_impl("run_command", {"command": 'v=NETLIFY_TOKEN; echo "${!v}" ${#NETLIFY_TOKEN}'})
    finally:
        done()
    assert "[NETLIFY_TOKEN]" in out and str(len(FAKE)) in out and FAKE not in out


def test_nothing_spoken_on_a_call_carries_a_key(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "_VAULT", _vault(tmp_path))

    class Relay:
        pass
    r = Relay()
    P._wire_openai(r)
    assert r.scrub(f"the token is {FAKE}") == "the token is [NETLIFY_TOKEN]"
