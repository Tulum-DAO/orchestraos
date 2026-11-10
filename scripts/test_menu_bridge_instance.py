"""Gateway menu-instance lookup: bind on matching signature, fail soft, one timeout per tick."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import menu_bridge as mb  # noqa: E402
import menu_instance as mi  # noqa: E402


CAP = {"question": "Pick one?", "options": [{"n": 1, "label": "Yes"}, {"n": 2, "label": "No"}]}


class _Resp:
    def __init__(self, body, status=200):
        self.status = status
        self._body = json.dumps(body).encode() if not isinstance(body, (bytes, str)) else (
            body if isinstance(body, bytes) else body.encode())

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_lookup_binds_when_signature_matches():
    sig = mi.signature(CAP)
    def opener(req, timeout=None):
        assert "menu-instance" in req.full_url
        assert req.headers.get("Authorization") == "Bearer tok"
        return _Resp({"ok": True, "instance": "inst-9", "signature": sig})
    assert mb.lookup_instance("seat-a", CAP, opener=opener, token="tok") == "inst-9"


def test_lookup_rejects_signature_mismatch():
    def opener(req, timeout=None):
        return _Resp({"ok": True, "instance": "inst-9", "signature": "deadbeefdeadbeef"})
    assert mb.lookup_instance("seat-a", CAP, opener=opener, token="tok") is None


def test_lookup_without_token_is_none():
    def opener(req, timeout=None):
        raise AssertionError("must not dial without a token")
    assert mb.lookup_instance("seat-a", CAP, opener=opener, token="") is None


def test_make_instance_fn_stops_after_one_transport_error():
    calls = {"n": 0}
    def opener(req, timeout=None):
        calls["n"] += 1
        raise URLError("down")
    fn = mb.make_instance_fn(opener=opener, token="tok")
    assert fn("s1", CAP) is None
    assert fn("s2", CAP) is None
    assert calls["n"] == 1


def test_bridge_one_stamps_instance_on_payload(monkeypatch):
    captured = {}
    class Store:
        def create(self, **kw):
            captured.update(kw)
            return "row-1"
    monkeypatch.setattr(mb.core, "should_bridge", lambda *a, **k: True)
    monkeypatch.setattr(mb.core, "needs_hydration", lambda c: False)
    monkeypatch.setattr(mb, "stamp_input_kinds" if hasattr(mb, "stamp_input_kinds") else "core", mb.core)
    # stamp_input_kinds is imported inside bridge_one from watch_gateway
    import types
    fake_wg = types.SimpleNamespace(stamp_input_kinds=lambda p: p)
    sys.modules["watch_gateway"] = fake_wg
    rid = mb.bridge_one(
        Store(), "seat-a", CAP, 0, 10, "idle",
        debounce_s=0, instance_fn=lambda sess, cap: "inst-7",
    )
    assert rid == "row-1"
    menu = json.loads(captured["menu"]) if isinstance(captured.get("menu"), str) else captured.get("menu")
    # store.create json-encodes menu
    if isinstance(captured.get("menu"), str):
        menu = json.loads(captured["menu"])
    else:
        # we passed a dict; create may have encoded after we captured
        menu = captured["menu"]
        if isinstance(menu, str):
            menu = json.loads(menu)
    assert menu["instance"] == "inst-7"


def test_token_file_uses_orchestra_dir_not_jarvis(monkeypatch, tmp_path):
    tok = tmp_path / "state" / "watch-gateway-token"
    tok.parent.mkdir()
    tok.write_text("abc\n")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.delenv("WATCH_GATEWAY_TOKEN_FILE", raising=False)
    assert "jarvis" not in mb._token_file()
    assert mb._gateway_token() == "abc"
