"""Rule 2 of the delete guard: protected FILES, decided on each delete target.

Refused: deleting an environment/secrets file, a credential or key file, a media file, anything in a
secrets folder (~/.secrets, ~/.ssh, ~/.gnupg) or in a folder the operator listed, and a recursive
delete of the current directory itself. Allowed: every command that only MENTIONS such a file
without deleting it, which the text-matching guard this replaces refused.

The guard is run as its hook runs (JSON on stdin), with the operator folder list in a temp file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
HOME = os.path.expanduser("~")
D = "rm "                                    # split so this file never reads as a delete itself
DR = "rm -" + "rf "


@pytest.fixture
def guarded(tmp_path):
    keep = tmp_path / "keep"
    (keep / "sub").mkdir(parents=True)
    f = tmp_path / "paths"
    f.write_text(f"# operator folders\n{keep}\n\nrelative/ignored\n")
    return keep, f


def run(cmd, paths_file, cwd="/srv/work"):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}
    env = {**os.environ, "DELETE_GUARD_PATHS_FILE": str(paths_file)}
    r = subprocess.run([sys.executable, str(HOOKS / "delete_guard.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    return r.returncode, r.stderr


REFUSED = [
    D + ".env", D + "-f api/.env", D + "config/.env.production", D + "deploy/prod.env",
    D + "server.pem", D + "tls/site.key", D + "cert.p12", D + "*.pem", D + "credentials.json",
    D + "~/.ssh/id_ed25519", D + "id_rsa.pub", D + "~/.secrets/telegram.token", DR + "~/.secrets",
    "unlink app.key", "shred -u .env", "sudo " + D + ".env", "sh -c '" + D + "-f .env'",
    "find . -name '*.pem' -delete", "find ~/.secrets -type f -delete",
    D + "song.mp3", D + "/tmp/clip.mp4", D + "-f voice.WAV",
    DR + ".", DR + "./",
    "cd /x && " + D + ".env",
]


@pytest.mark.parametrize("cmd", REFUSED)
def test_a_delete_of_a_protected_file_is_refused(cmd, guarded):
    rc, err = run(cmd, guarded[1])
    assert rc == 2, f"{cmd!r} was allowed"
    assert "protected-file" in err and "NOTHING WAS DELETED" in err and "~/.trash-safety/" in err


def test_the_operator_folders_come_from_the_data_dir_file(guarded):
    keep, f = guarded
    assert run(D + f"{keep}/sub/notes.txt", f)[0] == 2
    assert run(DR + str(keep), f)[0] == 2
    assert run(DR + str(keep.parent), f)[0] == 2          # a recursive delete that CONTAINS one
    assert run(D + str(keep.parent / "other.txt"), f)[0] == 0
    assert run(D + "-r " + str(keep.parent / "other"), f)[0] == 0
    assert run(D + "relative/ignored/x.txt", f)[0] == 0    # a relative line in the file is ignored


ALLOWED = [
    "echo 'run " + D + ".env later'",
    "git commit -m 'drop the credentials handling and the " + D + "server.pem step'",
    "grep -r credentials src/",
    "cat .env",
    "mv .env ~/.trash-safety/",
    "python3 msg_store.py send --from a --to b --body-file note.txt",
    "cat > note.txt <<'EOF'\n" + D + "-f .env.secrets\n" + DR + "~/.secrets\nEOF",
    D + ".env.example", D + ".env.sample", D + "notes.txt", DR + "build/", DR + "node_modules",
    "find . -name '*.pyc' -delete",
    D + "-f /tmp/scratch/out.json",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_a_command_that_only_mentions_or_deletes_something_else_is_allowed(cmd, guarded):
    rc, err = run(cmd, guarded[1])
    assert rc == 0, f"{cmd!r} was refused: {err}"


def test_a_missing_folder_list_only_drops_the_operator_folders(tmp_path):
    missing = tmp_path / "nope"
    assert run(D + ".env", missing)[0] == 2
    assert run(D + "notes.txt", missing)[0] == 0


def test_the_refusal_points_at_gm_never_at_a_person(guarded):
    _rc, err = run(D + ".env", guarded[1])
    assert "ask gm" in err and "operator" not in err.lower()


# ───────── variables (codex-gm review on #633): a target the guard cannot resolve is refused ─────────

OPAQUE_REFUSED = [
    D + '"$DELETE_TARGET"',
    DR + '"$UNSET_DIR/sub"',
    D + '"${SOMETHING}"',
    D + '"$(cat list.txt)"',
    D + "$(cat list.txt)",
    D + "`cat list.txt`",
    "for t in a b; do " + D + '"$t"; done',
    "X=/tmp/x " + D + '"$X/out.json"',          # a prefix assignment is not in effect for $X
    "S=/srv/app/.env; " + D + '"$S"',           # resolved, and it is a protected name
    "S=" + HOME + "/.secrets; " + D + '"$S/token"',
    "export K=" + HOME + "/.ssh && " + D + '"$K/id_rsa"',
    "S=$(mktemp -d); " + DR + '"$S"',           # value itself unknown
]


@pytest.mark.parametrize("cmd", OPAQUE_REFUSED)
def test_a_delete_target_the_guard_cannot_resolve_is_refused(cmd, guarded):
    rc, err = run(cmd, guarded[1])
    assert rc == 2, f"{cmd!r} was allowed"


VARS_ALLOWED = [
    "S=/tmp/scratch; " + D + '-f "$S/out.json"',
    "S=/tmp/scratch && " + DR + '"$S/build"',
    "export B=/srv/work/build; " + DR + '"${B}"',
    "S=/tmp/a; T=$S/b; " + D + '"$T/c.txt"',
    D + '"$HOME/notes.txt"',
    D + '"$PWD/old.log"',
    D + '"$TMPDIR/x.json"',
]


@pytest.mark.parametrize("cmd", VARS_ALLOWED)
def test_a_variable_set_earlier_in_the_command_resolves(cmd, guarded):
    rc, err = run(cmd, guarded[1])
    assert rc == 0, f"{cmd!r} was refused: {err}"


def test_the_unresolved_refusal_says_how_to_fix_it(guarded):
    _rc, err = run(D + '"$DELETE_TARGET"', guarded[1])
    assert "cannot resolve" in err and "name the path literally" in err


def test_a_delete_behind_a_shell_keyword_is_still_seen(guarded):
    assert run("for f in a b; do " + D + ".env; done", guarded[1])[0] == 2
    assert run("if true; then " + D + "server.pem; fi", guarded[1])[0] == 2
    assert run("for d in x; do " + DR + "/; done", guarded[1])[0] == 2       # the nuclear rule too
