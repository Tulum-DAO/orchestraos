"""CODE vs DATA for the lineage daemon (data-dir sweep S2).

Under `orchestra up` and scripts/run-beat.sh, ORCHESTRA_DIR (the `orchestra_dir` / `od` the beat passes
down) is the DATA dir: state/, logs/, registry.json, ~/.orchestra by default. The scripts the rotation
steps run (spawn-agent.sh, msg_store.py, scripts/*.py) are CODE: they live in the checkout and do not
exist under the data dir on a real install, so every armed rotation step failed with ENOENT.

  code_path(*parts)  a path inside the checkout: ORCHESTRA_ROOT, else this file's checkout. Read per
                     call, so a test (or a caller) that sets ORCHESTRA_ROOT is honoured.
  child_env(od)      the environment for a child that runs from the checkout but must act on the data
                     dir `od`. Several of those scripts fall back to "next to my own file" for their
                     data (registry-update.py, approval.py, spawn-agent.sh), which is now the checkout,
                     so the data dir is handed over explicitly instead.
"""
import os

_HERE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def code_root() -> str:
    return os.environ.get("ORCHESTRA_ROOT") or _HERE_ROOT


def code_path(*parts) -> str:
    return os.path.join(code_root(), *parts)


def child_env(orchestra_dir, base=None) -> dict:
    """`base` (default: this process's environment) with the data dir pinned to `orchestra_dir`
    (ORCHESTRA_DIR and its older name ORCH_DIR) and ORCHESTRA_ROOT set to the checkout."""
    env = dict(os.environ if base is None else base)
    if orchestra_dir:
        env["ORCHESTRA_DIR"] = env["ORCH_DIR"] = str(orchestra_dir)
    env.setdefault("ORCHESTRA_ROOT", code_root())
    return env
