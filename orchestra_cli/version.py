"""Installed vs latest OrchestraOS version (issue #104). Pure over an injected git runner
`git(argv, cwd) -> (rc, out)` so doctor/up/tests share one rule and run hermetically.

installed  = `git describe --tags --always` of the checkout (a tag, a tag-N-gsha, or a bare sha)
latest     = the highest v-semver tag in `git ls-remote --tags origin` (peeled ^{} refs and
             non-version names ignored)
behind     = installed's version tuple < latest's, when BOTH are known; else None
Every git failure is UNKNOWN (None), never an exception: a notice must never block `up` or
redden `doctor` by itself."""
from __future__ import annotations

import re
import subprocess

_VER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")


def default_git(argv, cwd=None):
    r = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, timeout=8)
    return r.returncode, (r.stdout or "") + (r.stderr if r.returncode else "")


def parse_version(s):
    """(major, minor, patch) from 'v0.10.1', 'v0.1.0-hackathon', 'v0.2.0-3-gabc'; None otherwise."""
    m = _VER.match((s or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def installed(root, *, git=default_git):
    try:
        rc, out = git(["describe", "--tags", "--always"], cwd=str(root))
    except Exception:  # noqa: BLE001 — unknown, never raise
        return None
    return out.strip().splitlines()[0] if rc == 0 and out.strip() else None


def latest_tag(root, *, git=default_git):
    try:
        rc, out = git(["ls-remote", "--tags", "--refs", "origin"], cwd=str(root))
    except Exception:  # noqa: BLE001
        return None
    if rc != 0:
        return None
    best, best_v = None, None
    for line in out.splitlines():
        if "\trefs/tags/" not in line:
            continue
        tag = line.split("\trefs/tags/", 1)[1].strip()
        if tag.endswith("^{}"):
            continue
        v = parse_version(tag)
        if v is not None and (best_v is None or v > best_v):
            best, best_v = tag, v
    return best


def status(root, *, git=default_git):
    inst, lat = installed(root, git=git), latest_tag(root, git=git)
    iv, lv = parse_version(inst), parse_version(lat)
    behind = (iv < lv) if (iv is not None and lv is not None) else None
    return {"installed": inst, "latest": lat, "behind": behind}


def notice_line(st):
    if not st or not st.get("behind"):
        return ""
    return (f"update available: installed {st['installed']}, latest {st['latest']} — "
            f"run `orchestra upgrade` (seats keep their code until their next spawn/rotation)")
