# RED-first handler-level tests for the inject_message two-phase invariant (Bug 1).
#
# Fixtures come from the REAL send-keys seam: we monkeypatch mod._run_on_machine (the single choke
# point every tmux command in arturo-proxy flows through) with a SPY that records the exact command
# strings and returns controlled capture output. No hand-typed command expectations.

import importlib.util
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Spy:
    """Records every (mac_cmd, vps_cmd) that hits _run_on_machine. Answers capture-pane commands
    with a scripted pane so we can drive the landed/not-landed branch; answers everything else OK."""

    def __init__(self, pane_sequence):
        self.cmds = []                      # list of vps_cmd strings actually issued
        self._panes = list(pane_sequence)   # capture responses, consumed in order (last repeats)

    def __call__(self, mac_cmd, vps_cmd, timeout=15, prefer_mac=False):
        self.cmds.append(vps_cmd)
        if "capture-pane" in vps_cmd:
            pane = self._panes.pop(0) if len(self._panes) > 1 else self._panes[0]
            return True, pane, "vps"
        return True, "", "vps"

    # helpers over the recorded log
    def sendkey_cmds(self):
        return [c for c in self.cmds if "send-keys" in c]

    def paste_cmds(self, body_snippet):
        return [c for c in self.sendkey_cmds() if body_snippet in c and " -l " in f" {c} "]

    def submit_cmds(self):
        return [c for c in self.sendkey_cmds() if c.rstrip().endswith("C-m")]


def _call_inject(mod, message, spy):
    mod._run_on_machine = spy            # patch the choke point
    # drive the tool handler directly
    return mod.execute_tool("inject_message",
                            {"session_name": "target-sess", "message": message})


BODY = "run the parity sweep and report the count"
LANDED = "\n---\n> \n---\n  model . 10%\n"                     # cleared composer
STRANDED = "\n---\n> [Arturo Voice]: " + BODY + "\n---\n  model . 10%\n"  # text stuck in composer


def test_inject_is_two_phase_paste_then_cr():
    mod = _load_proxy()
    spy = _Spy([LANDED])
    _call_inject(mod, BODY, spy)
    sk = spy.sendkey_cmds()
    # exactly one literal paste of the body, and it carries NO Enter/C-m
    pastes = spy.paste_cmds(BODY)
    assert len(pastes) == 1, f"expected 1 literal paste, got {len(pastes)}: {sk}"
    assert "Enter" not in pastes[0] and "C-m" not in pastes[0]
    # a standalone C-m submit is issued AFTER the paste
    submits = spy.submit_cmds()
    assert len(submits) >= 1, f"no standalone C-m submit issued: {sk}"
    assert sk.index(pastes[0]) < sk.index(submits[0]), "submit must come AFTER the paste"


def test_inject_never_uses_single_call_text_plus_enter():
    mod = _load_proxy()
    spy = _Spy([LANDED])
    _call_inject(mod, BODY, spy)
    # the OLD bug: one command containing BOTH the body and the Enter keyword
    for c in spy.sendkey_cmds():
        assert not (BODY in c and "Enter" in c), f"single-call text+Enter regression: {c}"


def test_retry_on_unverified_sends_cr_only_never_repastes():
    mod = _load_proxy()
    # first capture shows STRANDED (not landed) -> must retry; retry must be C-m only
    spy = _Spy([STRANDED, STRANDED])
    _call_inject(mod, BODY, spy)
    pastes = spy.paste_cmds(BODY)
    assert len(pastes) == 1, (
        f"DUPLICATE BUG: body pasted {len(pastes)}x -- retry re-pasted: {spy.sendkey_cmds()}")
    # and at least two C-m submits happened (initial + retry)
    assert len(spy.submit_cmds()) >= 2, f"retry did not send a C-m: {spy.sendkey_cmds()}"
