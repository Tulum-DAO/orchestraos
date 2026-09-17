#!/usr/bin/env python3
"""ensure_cwd_trusted.py <dir> — pre-seed ~/.claude.json so an INTERACTIVE claude
spawn in <dir> does NOT stall at the Claude Code workspace-trust dialog.

ROOT CAUSE (gm-g44, 2026-09-05, first-real-BG-green frozen at trust):
`--dangerously-skip-permissions` does NOT skip the workspace-trust dialog for an
INTERACTIVE (TTY) session. Per `claude --help`, that dialog is auto-skipped only
in NON-interactive mode (-p / non-TTY / piped). Agents run interactive in tmux,
so a spawn whose cwd lacks `projects[<dir>].hasTrustDialogAccepted == true` in
~/.claude.json freezes at "Is this a project you trust? [No, exit / Yes, I trust
this folder]" with no human to answer it. Orchestra-dir agents never hit it
because that dir is already trusted; a BG green (or any spawn) in a NON-trusted
cwd like ~/repos/<x> stalls forever, and a deterministic boot-probe can't detect
it (the LLM never starts).

Fix: idempotently set the trust bit for <dir> BEFORE launching claude. flock the
config to serialize concurrent spawns; atomic-replace on write (torn-read safe).
Fail-soft: NEVER block a spawn — any error just warns and returns 0.
"""
import fcntl
import json
import os
import sys
import tempfile


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        return 0
    target = os.path.abspath(os.path.expanduser(sys.argv[1]))
    # Honor CLAUDE_CONFIG_DIR: claude reads <config dir>/.claude.json when it is set.
    cfg_dir_env = os.environ.get("CLAUDE_CONFIG_DIR")
    cfg = os.path.join(cfg_dir_env, ".claude.json") if cfg_dir_env else os.path.expanduser("~/.claude.json")
    try:
        if cfg_dir_env:
            os.makedirs(cfg_dir_env, exist_ok=True)
        fd = os.open(cfg, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 128 * 1024 * 1024).decode() or ""
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                return 0  # unexpected shape — do not clobber
            projects = data.setdefault("projects", {})
            if not isinstance(projects, dict):
                return 0
            entry = projects.setdefault(target, {})
            if not isinstance(entry, dict):
                return 0
            if entry.get("hasTrustDialogAccepted") is True:
                return 0  # already trusted — no write, no churn
            entry["hasTrustDialogAccepted"] = True
            dirn = os.path.dirname(cfg) or "."
            tf = tempfile.NamedTemporaryFile("w", dir=dirn, delete=False)
            try:
                json.dump(data, tf, indent=2)
                tf.flush()
                os.fsync(tf.fileno())
                tf.close()
                os.replace(tf.name, cfg)  # atomic; torn-read safe
            except Exception:
                try:
                    os.unlink(tf.name)
                except OSError:
                    pass
                raise
            sys.stderr.write(f"ensure_cwd_trusted: trusted {target}\n")
            return 0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception as e:  # fail-soft: never block a spawn
        sys.stderr.write(f"ensure_cwd_trusted: soft-fail for {target}: {e}\n")
        return 0


def ensure_bypass_accepted() -> int:
    """Pre-seed the Bypass Permissions acceptance ("Yes, I accept" on a fresh user writes
    skipDangerousModePermissionPrompt=true to ~/.claude/settings.json). Without it an
    unattended `claude --dangerously-skip-permissions` in tmux stops at that dialog, whose
    default is "No, exit", and the injected task lands in the shell (B3 container, 2026-09-17).
    Merge-only, atomic, fail-soft; no write when already set."""
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    cfg = os.path.join(cfg_dir, "settings.json")
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        fd = os.open(cfg, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 16 * 1024 * 1024).decode() or ""
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                return 0
            if data.get("skipDangerousModePermissionPrompt") is True:
                return 0
            data["skipDangerousModePermissionPrompt"] = True
            tf = tempfile.NamedTemporaryFile("w", dir=cfg_dir, delete=False)
            try:
                json.dump(data, tf, indent=2)
                tf.flush()
                os.fsync(tf.fileno())
                tf.close()
                os.replace(tf.name, cfg)
            except Exception:
                try:
                    os.unlink(tf.name)
                except OSError:
                    pass
                raise
            sys.stderr.write("ensure_cwd_trusted: pre-seeded bypass-permissions acceptance\n")
            return 0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception as e:  # fail-soft: never block a spawn
        sys.stderr.write(f"ensure_cwd_trusted: bypass pre-seed soft-fail: {e}\n")
        return 0


if __name__ == "__main__":
    main()
    sys.exit(ensure_bypass_accepted())
