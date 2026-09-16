"""RED-first tests for git_hygiene — Green's post-promote cwd hygiene, WORKTREE-AWARE.

Green's bootstrap (spec §3.4.6) does a git-status uncommitted-tree check + stale
`.git/index.lock` cleanup. The FIRST live Blue-Green target (identity-store-builder)
runs on a git WORKTREE cwd, where `.git` is a FILE ("gitdir: <path>") and the real
index.lock lives at `<main>/.git/worktrees/<name>/index.lock` — NOT `<cwd>/.git/
index.lock` (which does not exist). A naive cleanup keyed on `<cwd>/.git/` MISSES
the lock in a worktree = the unmodeled case. This helper resolves the REAL gitdir
for BOTH a main checkout and a worktree.

Stale-lock removal is guarded: only remove a lock with NO live holder (a live
git process owns it) — never yank a lock out from under an active operation.
"""
import os
import subprocess
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.git_hygiene import (  # noqa: E402
    resolve_gitdir, index_lock_path, has_uncommitted, stale_index_lock)


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def _main_repo(tmp_path):
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "init")
    return repo


def _worktree(tmp_path, main):
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", str(wt), "-b", "feature")
    return wt


def test_resolve_gitdir_main_checkout(tmp_path):
    main = _main_repo(tmp_path)
    gd = resolve_gitdir(str(main))
    assert os.path.realpath(gd) == os.path.realpath(str(main / ".git"))


def test_resolve_gitdir_worktree_follows_gitfile(tmp_path):
    main = _main_repo(tmp_path)
    wt = _worktree(tmp_path, main)
    gd = resolve_gitdir(str(wt))
    # a worktree's real gitdir is under <main>/.git/worktrees/<name>
    assert "worktrees" in gd
    assert os.path.realpath(gd).startswith(
        os.path.realpath(str(main / ".git" / "worktrees")))


def test_index_lock_path_worktree_is_under_main_gitdir(tmp_path):
    main = _main_repo(tmp_path)
    wt = _worktree(tmp_path, main)
    lock = index_lock_path(str(wt))
    # NOT <wt>/.git/index.lock (which is not a dir); under the real gitdir
    assert lock != os.path.join(str(wt), ".git", "index.lock")
    assert "worktrees" in lock and lock.endswith("index.lock")


def test_index_lock_path_main_checkout(tmp_path):
    main = _main_repo(tmp_path)
    lock = index_lock_path(str(main))
    assert os.path.realpath(lock) == os.path.realpath(
        str(main / ".git" / "index.lock"))


def test_has_uncommitted_detects_dirty_worktree(tmp_path):
    main = _main_repo(tmp_path)
    wt = _worktree(tmp_path, main)
    assert has_uncommitted(str(wt)) is False
    (wt / "new.txt").write_text("dirty")
    assert has_uncommitted(str(wt)) is True


def test_stale_index_lock_detected_in_worktree(tmp_path):
    main = _main_repo(tmp_path)
    wt = _worktree(tmp_path, main)
    lock = index_lock_path(str(wt))
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    open(lock, "w").close()          # simulate an orphaned lock from a killed Blue
    assert stale_index_lock(str(wt)) == lock   # found at the worktree gitdir


def test_no_lock_returns_none(tmp_path):
    main = _main_repo(tmp_path)
    wt = _worktree(tmp_path, main)
    assert stale_index_lock(str(wt)) is None
