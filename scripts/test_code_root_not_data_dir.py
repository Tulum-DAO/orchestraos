"""CODE vs DATA (gm msg_c1a15b1a). Under `orchestra up` ORCHESTRA_DIR is the DATA dir (~/.orchestra by
default; orchestra_cli/settings.py) and the checkout is elsewhere. Code resolved from ORCHESTRA_DIR does
not exist on a real install. Each check runs in a fresh interpreter with ORCHESTRA_DIR pointed at an
EMPTY temp dir and PYTHONPATH stripped, so `orchestra up`'s PYTHONPATH cannot hide an import bug.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(code, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "ORCHESTRA_ROOT", "ORCH_DIR")}
    env["ORCHESTRA_DIR"] = str(data)
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return r.stdout.strip()


def test_message_router_imports_its_code_from_the_checkout(tmp_path):
    # message-router.py imports msg_store (repo root) and scripts.identity_store at import time
    out = _run("import importlib.util as u;"
               f"s=u.spec_from_file_location('mr', {str(ROOT / 'scripts' / 'message-router.py')!r});"
               "m=u.module_from_spec(s); s.loader.exec_module(m); print('ok')", tmp_path)
    assert out.endswith("ok")


def test_s3_seam_loads_rotation_gate_manual_from_the_checkout(tmp_path):
    out = _run(f"import sys; sys.path.insert(0, {str(ROOT / 'scripts' / 'lineage_daemon')!r});"
               "import s3_live_seams as s; RG = s._import_rgm(); print(RG.__file__)", tmp_path)
    assert pathlib.Path(out).resolve().parent == ROOT / "scripts"


def test_voice_usage_card_runs_the_checkouts_approval_py(tmp_path):
    out = _run(f"import sys; sys.path.insert(0, {str(ROOT)!r});"
               "from services.arturo import voice_usage as vu; print(vu.CODE_ROOT / 'scripts' / 'approval.py')", tmp_path)
    assert pathlib.Path(out).is_file(), out
    assert not out.startswith(str(tmp_path)), "the notifier must not be looked for under the data dir"
