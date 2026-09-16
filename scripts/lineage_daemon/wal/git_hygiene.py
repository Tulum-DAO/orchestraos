"""git_hygiene — WORKTREE-AWARE cwd hygiene for Green's post-promote bootstrap.

Spec §3.4.6: an evicted/SIGKILLed Blue can orphan `.git/index.lock`, and Green's
first act checks for uncommitted tree state + cleans stale locks. The FIRST live
Blue-Green target (identity-store-builder) runs on a git WORKTREE, where `.git`
is a FILE ("gitdir: <path>") and the real index.lock lives under
`<main>/.git/worktrees/<name>/`, NOT `<cwd>/.git/`. Keying cleanup on `<cwd>/.git`
would silently MISS the lock in a worktree — the unmodeled case. This module
resolves the REAL gitdir for both a main checkout and a worktree by asking git
itself (`rev-parse --git-dir`), so it is correct by construction for either.

Stale-lock removal is guarded elsewhere (live-holder check): this module only
LOCATES + DETECTS; the caller verifies no live git process holds it (fuser/pid)
before removing — never yank a lock from an active operation.
"""
import os
import subprocess


def _git_out(cwd, *args):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def resolve_gitdir(cwd):
    """The REAL git directory for cwd — the per-worktree gitdir for a worktree
    (`<main>/.git/worktrees/<name>`), or `<repo>/.git` for a main checkout.
    Uses `git rev-parse --absolute-git-dir` so a `.git`-FILE worktree is followed
    correctly (never assumes `<cwd>/.git` is a directory)."""
    gd = _git_out(cwd, "rev-parse", "--absolute-git-dir")
    if gd:
        return gd
    # fallback: read the .git file/dir directly
    dot = os.path.join(cwd, ".git")
    if os.path.isfile(dot):
        with open(dot) as fh:
            line = fh.read().strip()
        if line.startswith("gitdir:"):
            p = line.split(":", 1)[1].strip()
            return p if os.path.isabs(p) else os.path.normpath(
                os.path.join(cwd, p))
    return dot


def index_lock_path(cwd):
    """Absolute path of the index.lock for cwd's git tree, worktree-correct."""
    return os.path.join(resolve_gitdir(cwd), "index.lock")


def has_uncommitted(cwd):
    """True iff the working tree has uncommitted changes (tracked or untracked).
    Runs in the actual cwd — a worktree's dirty state, not the main checkout's."""
    out = _git_out(cwd, "status", "--porcelain")
    return bool(out) if out is not None else False


def stale_index_lock(cwd):
    """Return the index.lock path if it EXISTS (a candidate orphan from a killed
    Blue), else None. Existence only — the caller confirms no live holder before
    removing (fuser/pid check), so an active git op is never disrupted."""
    lock = index_lock_path(cwd)
    return lock if os.path.exists(lock) else None
