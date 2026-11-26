"""The pre-push secret scan (.git-hooks/pre-push): scans only what the push changes, the whole tree
when the baseline changed, and queues behind a machine-wide lock instead of stacking.

Runs the real hook against a scratch repo with a fake detect-secrets-hook that records its arguments."""
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".git-hooks" / "pre-push"
ZERO = "0" * 40


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    for name in ("a.py", "b.py", ".secrets.baseline"):
        (r / name).write_text("x\n")
    (r / ".gitignore").write_text(".venv/\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    venv = r / ".venv" / "bin"
    venv.mkdir(parents=True)
    fake = venv / "detect-secrets-hook"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/scanned\nsleep ${{FAKE_SCAN_SECONDS:-0}}\nexit 0\n")
    fake.chmod(0o755)
    return r


def push(repo, tmp_path, local, remote, extra_env=None):
    env = {**os.environ, "ORCHESTRA_GATE_LOCK": str(tmp_path / "gate.lock"), **(extra_env or {})}
    return subprocess.Popen(["bash", str(HOOK), "origin", "url"], cwd=repo, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env), \
        f"refs/heads/x {local} refs/heads/x {remote}\n"


def run(repo, tmp_path, local, remote, extra_env=None):
    p, line = push(repo, tmp_path, local, remote, extra_env)
    out, err = p.communicate(line, timeout=60)
    return p.returncode, err


def scanned(tmp_path):
    f = tmp_path / "scanned"
    return [l.split() for l in f.read_text().splitlines()] if f.exists() else []


def test_a_push_scans_only_the_files_it_changes(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("y\n")
    (repo / "c.py").write_text("new\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change")
    rc, err = run(repo, tmp_path, git(repo, "rev-parse", "HEAD"), base)
    assert rc == 0, err
    [args] = scanned(tmp_path)
    assert sorted(args[2:]) == ["a.py", "c.py"], args
    assert "2 changed file(s)" in err


def test_a_new_branch_is_compared_with_where_it_left_main(repo, tmp_path):
    git(repo, "switch", "-qc", "feature")
    (repo / "b.py").write_text("z\n")
    git(repo, "commit", "-qam", "feature")
    rc, _ = run(repo, tmp_path, git(repo, "rev-parse", "HEAD"), ZERO)
    assert rc == 0
    assert scanned(tmp_path)[0][2:] == ["b.py"]


def test_a_baseline_change_scans_the_whole_tree(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / ".secrets.baseline").write_text("changed\n")
    git(repo, "commit", "-qam", "baseline")
    rc, err = run(repo, tmp_path, git(repo, "rev-parse", "HEAD"), base)
    assert rc == 0
    assert sorted(scanned(tmp_path)[0][2:]) == [".gitignore", ".secrets.baseline", "a.py", "b.py"]
    assert "the whole tree" in err


def test_a_deleted_file_or_a_branch_deletion_scans_nothing(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "rm", "-q", "a.py")
    git(repo, "commit", "-qm", "rm")
    assert run(repo, tmp_path, git(repo, "rev-parse", "HEAD"), base)[0] == 0
    assert run(repo, tmp_path, ZERO, base)[0] == 0
    assert scanned(tmp_path) == []


def test_two_pushes_queue_on_the_lock_instead_of_scanning_at_once(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("y\n")
    git(repo, "commit", "-qam", "change")
    head = git(repo, "rev-parse", "HEAD")
    slow = {"FAKE_SCAN_SECONDS": "2"}
    p1, l1 = push(repo, tmp_path, head, base, slow)
    p1.stdin.write(l1)
    p1.stdin.close()
    time.sleep(0.5)
    t0 = time.time()
    rc2, err2 = run(repo, tmp_path, head, base, slow)
    p1.wait(timeout=30)
    assert rc2 == 0 and p1.returncode == 0
    assert "waiting for the heavy-gate lock held by pid" in err2
    assert time.time() - t0 >= 3, "the second scan ran only after the first released the lock"


# --- trailer addresses: a sign-off is the author's own address or a noreply one ---------------------------

def _commit(repo, body):
    (repo / "a.py").write_text(body[:20] + "\n")
    git(repo, "commit", "-qam", body)
    return git(repo, "rev-parse", "HEAD")


def test_a_sign_off_with_the_authors_address_or_a_noreply_passes(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "x\n\nSigned-off-by: t <t@example.com>\nCo-Authored-By: Bot <noreply@example.org>")
    rc, err = run(repo, tmp_path, head, base)
    assert rc == 0, err


def test_a_hand_typed_sign_off_with_another_address_blocks_the_push(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "x\n\nSigned-off-by: Someone <someone@private.example>")
    rc, err = run(repo, tmp_path, head, base)
    assert rc == 1 and "someone@private.example" in err and head[:8] in err
    assert scanned(tmp_path) == []                                          # refused before the scan


def test_the_trailer_check_runs_even_without_detect_secrets(repo, tmp_path):
    (repo / ".venv" / "bin" / "detect-secrets-hook").unlink()
    base = git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "x\n\nco-authored-by: A <a@private.example>")
    rc, err = run(repo, tmp_path, head, base, {"PATH": "/usr/bin:/bin"})
    assert rc == 1 and "a@private.example" in err


def test_a_locally_listed_private_address_is_refused_anywhere_in_the_message(repo, tmp_path):
    git(repo, "config", "--add", "orchestra.privateEmail", "me@home.example")
    base = git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "x\n\nmail me at ME@home.example")
    rc, err = run(repo, tmp_path, head, base)
    assert rc == 1 and "private address" in err and "me@home.example" not in err.lower()


def test_only_the_pushed_range_is_checked(repo, tmp_path):
    _commit(repo, "old\n\nSigned-off-by: Someone <someone@private.example>")   # already on the remote
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    base = git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "new\n\nSigned-off-by: t <t@example.com>")
    rc, err = run(repo, tmp_path, head, base)
    assert rc == 0, err
