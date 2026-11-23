"""`orchestra upgrade` — bring the checkout to origin and re-run the idempotent setup.

Sequence (docs/UPGRADE.md): fetch; show the incoming commits and flag the contract-bearing
paths other running components trust (scripts/lineage_daemon/, msg_store.py,
scripts/approval*.py); refuse on a dirty checkout; `git pull --ff-only`; `orchestra init
--yes` (config kept, hooks re-pointed at the new checkout, builds refreshed); `orchestra
doctor`. Spawned seats are untouched: a live tmux seat keeps the code it was spawned with
until its next spawn/rotation, and the supervisor picks up new code only on
`orchestra down && orchestra up`. This command never restarts either.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import login_guard as LG

CONTRACT_PATHS = (re.compile(r"^scripts/lineage_daemon/"), re.compile(r"^msg_store\.py$"),
                  re.compile(r"^scripts/approval[^/]*\.py$"))


def default_git(argv, cwd=None):
    r = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, check=False)
    return r.returncode, (r.stdout or "") + (r.stderr if r.returncode else "")


def default_init(root: Path, **kw) -> list:
    from .init_cmd import render_report, run_init
    report = run_init(root, **kw)
    print(render_report(report))
    return report


def default_doctor(root: Path) -> int:
    from . import doctor as D
    from .settings import load_settings
    st = load_settings(root)
    checks = D.run_doctor(st, D.default_probes(st))
    print(D.render_table(checks))
    return D.exit_code(checks)


@dataclass
class UpgradeReport:
    ok: bool = True
    exit_code: int = 0
    dry_run: bool = False
    incoming: list = field(default_factory=list)
    contract_paths: list = field(default_factory=list)
    error: Optional[str] = None
    pulled: bool = False
    doctor_rc: Optional[int] = None
    logins: str = ""                 # the login guard's one-line verdict

    def render(self) -> str:
        lines = []
        if self.error:
            lines.append(f"upgrade stopped: {self.error}")
        if self.incoming:
            lines.append(f"incoming: {len(self.incoming)} commit(s)")
            lines += [f"  {c}" for c in self.incoming]
        elif not self.error:
            lines.append("incoming: already up to date with origin")
        if self.contract_paths:
            lines.append("contract-bearing paths changed (running components trust these; read the commits above):")
            lines += [f"  {p}" for p in self.contract_paths]
        if self.dry_run:
            lines.append("dry run: nothing pulled, init and doctor not run")
        elif self.pulled and not self.error:
            lines.append("pulled --ff-only; init --yes and doctor ran (see above)")
        if self.logins:
            lines.append(f"logins: {self.logins}")
        if not self.error:
            lines.append("Seats are untouched: live tmux seats keep the code they were spawned with until their next "
                         "spawn/rotation. To run the new code in the services and beats: `orchestra down && orchestra up --detach`.")
        return "\n".join(lines)


LOGIN_EXIT = 3


def run_upgrade(repo_root: Path, *, git: Callable = default_git, init: Callable = default_init,
                doctor: Callable = default_doctor, dry_run: bool = False,
                init_kwargs: Optional[dict] = None, guard: Optional["LG.LoginGuard"] = None) -> UpgradeReport:
    root = Path(repo_root)
    rep = UpgradeReport(dry_run=dry_run)
    # The login guard: fingerprint every provider login file before anything runs; re-check after every
    # phase. A phase that harmed one stops the upgrade with exit 3 and a LOGIN LOST line naming the file
    # and the phase. Nothing here ever writes a login file (see login_guard.py).
    guard = (guard or LG.LoginGuard()).begin()
    try:
        from .settings import load_settings
        stale = guard.load_baked(LG.stamp_path_for(load_settings(root).data_dir), step="baked-login-dirs")
    except Exception:  # noqa: BLE001 — no readable config: nothing was baked for this data dir
        stale = []
    if stale:
        for f in stale:
            print(f.line(), flush=True)
        rep.ok, rep.exit_code, rep.logins = False, LOGIN_EXIT, guard.summary()
        rep.error = f"{stale[0].line()}. Nothing was pulled."
        return rep

    def harmed(phase: str) -> bool:
        bad = guard.after(phase)
        for f in bad:
            print(f.line(), flush=True)
        if bad:
            rep.ok, rep.exit_code = False, LOGIN_EXIT
            rep.error = f"{bad[0].line()}. Log in again with that provider's CLI; this upgrade did not write any login file."
            rep.logins = guard.summary()
        return bool(bad)

    rc, out = git(["git", "fetch", "--quiet", "origin"], cwd=root)
    if rc != 0:
        rep.ok, rep.exit_code, rep.error = False, 1, f"git fetch failed: {out.strip()[:200]}"
        return rep
    if harmed("fetch"):
        return rep
    _, log = git(["git", "log", "--oneline", "HEAD..@{u}"], cwd=root)
    rep.incoming = [l for l in (log or "").splitlines() if l.strip()]
    _, names = git(["git", "diff", "--name-only", "HEAD..@{u}"], cwd=root)
    rep.contract_paths = [p for p in (names or "").splitlines() if p.strip() and any(rx.search(p) for rx in CONTRACT_PATHS)]
    if dry_run:
        return rep
    _, dirty = git(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root)
    if (dirty or "").strip():
        rep.ok, rep.exit_code = False, 2
        rep.error = ("the checkout has uncommitted changes; commit or discard them first "
                     f"({len(dirty.strip().splitlines())} file(s) — `git status`)")
        return rep
    if rep.incoming:
        rc, out = git(["git", "pull", "--ff-only", "--quiet"], cwd=root)
        if rc != 0:
            rep.ok, rep.exit_code = False, 1
            rep.error = f"git pull --ff-only failed (local branch diverged from origin?): {out.strip()[:200]}"
            return rep
        rep.pulled = True
        if harmed("pull"):
            return rep
    init(root, yes=True, **(init_kwargs or {}))
    if harmed("init"):
        return rep
    rep.doctor_rc = doctor(root)
    if harmed("doctor"):
        return rep
    rep.exit_code = 0 if rep.doctor_rc == 0 else rep.doctor_rc
    rep.ok = rep.exit_code == 0
    try:
        from .settings import load_settings
        guard.stamp_path = LG.stamp_path_for(load_settings(root).data_dir)
        guard.seal("upgrade")
    except Exception:  # noqa: BLE001 — the stamp is for doctor; init already wrote one
        pass
    rep.logins = guard.summary()
    return rep
