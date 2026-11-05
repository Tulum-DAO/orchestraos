"""A scratch gateway for the attention tests: scratch ledger + stores, a fleet bearer and a read-only device.

Not a test module itself (no test_ prefix). Nothing here touches a live pane or the live ledger: the stores
are bound to a tmp_path DB, the permission scan is stubbed, and the install person is fixed."""
import asyncio
import sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402
import attention_store as S  # noqa: E402
import notify_policy  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from questionnaire_schema import QuestionnaireStore  # noqa: E402
from scripts.device_tokens import DeviceStore  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"


class Box:
    def __init__(self, tmp_path, monkeypatch, armed=True):
        self.data = tmp_path / "data"
        (self.data / "state").mkdir(parents=True, exist_ok=True)
        tok = tmp_path / "gateway-token"
        tok.write_text(FLEET)
        if armed:
            monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
        else:
            monkeypatch.setenv("APPROVAL_DDL_ARMED", "m20251010_nothing")
            monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ORCHESTRA_DIR", str(self.data))
        monkeypatch.setattr(G, "ORCH_DIR", self.data)
        monkeypatch.setattr(G, "TOKEN_FILE", tok)
        self.db = str(self.data / "state" / "tasks.db")
        self.ledger = ApprovalStore(db_path=self.db); self.ledger.migrate()
        self.qstore = QuestionnaireStore(db_path=self.db); self.qstore.migrate()
        self.astore = S.AttentionStore(db_path=self.db); self.astore.migrate()
        monkeypatch.setattr(G, "ApprovalStore", lambda *a, **k: ApprovalStore(db_path=self.db))
        monkeypatch.setattr(G, "QuestionnaireStore", lambda *a, **k: QuestionnaireStore(db_path=self.db))
        monkeypatch.setattr(G, "AttentionStore", lambda *a, **k: S.AttentionStore(db_path=self.db))
        self.perm_rows = []
        monkeypatch.setattr(G, "_perm_pseudo_rows", lambda: list(self.perm_rows))
        monkeypatch.setattr(notify_policy, "install_user_and_zone", lambda raw=None: ("alex", "UTC"))
        monkeypatch.setitem(G._agents_cache, "data", [])
        dev = DeviceStore(self.data / "state" / "devices")
        _, self.read_only = dev.mint("Watch", ["read"])
        _, self.approver = dev.mint("Phone", ["read", "approve"])

    def auth(self, who="fleet"):
        tok = {"fleet": FLEET, "read": self.read_only, "approve": self.approver}[who]
        return {"Authorization": f"Bearer {tok}"}

    def call(self, method, path, who="fleet", body=None, params=None):
        async def go():
            async with TestClient(TestServer(G.build_app())) as c:
                r = await c.request(method, path, json=body, headers=self.auth(who), params=params)
                try:
                    return r.status, await r.json()
                except Exception:  # noqa: BLE001
                    return r.status, None
        return asyncio.run(go())
