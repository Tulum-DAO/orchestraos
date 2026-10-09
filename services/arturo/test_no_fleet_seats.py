"""Arturo ships to every install, so what it says, offers and signs names no seat of a particular
fleet: the live model's deep-brain tool and prompt point at "this install's manager", and its
notices are signed "arturo"."""
import pathlib

from services.arturo import gemini_live_bridge as GLB

FLEET_SEATS = ("gemini-orchestra-dev", "gemini-gm", "arturo-voice")


def test_the_live_models_tools_name_no_fleet_seat():
    for decl in GLB.TOOL_DECLARATIONS:
        for name in FLEET_SEATS:
            assert name not in decl.get("description", ""), (decl["name"], name)


def test_the_live_prompt_and_the_notices_name_no_fleet_seat():
    for path in ("services/arturo/gemini_live_bridge.py", "services/arturo/arturo-proxy.py"):
        src = pathlib.Path(path).read_text()
        assert "'gemini-orchestra-dev'" not in src and "(gemini-orchestra-dev)" not in src, path
        assert '"--from", "arturo-voice"' not in src, path
