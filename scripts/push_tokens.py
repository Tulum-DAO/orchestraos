"""Push-notification device tokens, as registered by the paired apps.

One JSON file (<data dir>/state/push-tokens.json, 0600), keyed by the APNs token. Each record
says WHO registered it (the caller's principal: a device id, or "legacy" for the shared fleet
bearer), for WHICH app (bundle_id is the APNs topic, and the Watch is a different app from the
phone) and for WHICH APNs host (env: an App Store / TestFlight build registers a production
token, an Xcode build a sandbox one; one global setting breaks a mixed fleet).

Three rules this module exists to keep:

1. NO DEFAULTS. platform, bundle_id and env are required. With the shared fleet bearer, platform
   is the only thing telling the phone, iPad and watch apart, and a wrong topic or host is a
   push APNs rejects.
2. A STALE FORGET CANNOT UNREGISTER A RE-PAIRED DEVICE. An APNs token belongs to the app
   install, not to the pairing, so "Forget, then pair again without reinstalling" yields the
   SAME token. Every write carries the client's monotonic `rev`; a remove applies only when its
   rev is at least the stored one.
3. TOKENS DIE WITH THEIR DEVICE. A token whose device no longer resolves (revoked, or deleted)
   is skipped and pruned by `live()`. That covers every revoke path (CLI, rotation, upgrade
   replacing a label) without hooking each of them.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

PLATFORMS = ("iphone", "ipad", "watch")
ENVS = ("sandbox", "production")
#: The published app's two topics. An install that ships its own build overrides this with
#: ORCHESTRA_PUSH_BUNDLE_IDS. It is an allowlist so a client cannot make the sender push under
#: an arbitrary topic.
DEFAULT_BUNDLE_IDS = ("co.bizypro.OrchestraOS", "co.bizypro.OrchestraOS.watchkitapp")
#: The fleet bearer's principal id (watch_gateway.resolve_principal).
LEGACY_PRINCIPAL = "legacy"

_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32,200}$")


class PushTokenError(ValueError):
    """A registration that is missing a field or names a value we do not accept."""


def allowed_bundle_ids() -> tuple[str, ...]:
    raw = os.environ.get("ORCHESTRA_PUSH_BUNDLE_IDS", "")
    got = tuple(b.strip() for b in raw.split(",") if b.strip())
    return got or DEFAULT_BUNDLE_IDS


def _token(value) -> str:
    tok = str(value or "").strip()
    if not _TOKEN_RE.match(tok):
        raise PushTokenError("token must be the APNs device token as hex")
    return tok.lower()


def _rev(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PushTokenError("rev must be a non-negative integer that only ever grows")
    return value


def validate_registration(data: dict) -> dict:
    """The record fields from a PUT body, or PushTokenError naming the first bad one."""
    if not isinstance(data, dict):
        raise PushTokenError("body must be a JSON object")
    platform = str(data.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        raise PushTokenError(f"platform must be one of {', '.join(PLATFORMS)}")
    env = str(data.get("env") or "").strip().lower()
    if env not in ENVS:
        raise PushTokenError(f"env must be one of {', '.join(ENVS)}")
    bundle_id = str(data.get("bundle_id") or "").strip()
    if bundle_id not in allowed_bundle_ids():
        raise PushTokenError("bundle_id is not an app this gateway sends to")
    return {"token": _token(data.get("token")), "platform": platform, "env": env,
            "bundle_id": bundle_id, "rev": _rev(data.get("rev"))}


class PushTokenStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)

    def register(self, principal_id: str, fields: dict) -> bool:
        """Store or refresh a token. Returns False (and changes nothing) when a NEWER rev is
        already on file, so a delayed registration cannot undo a later Forget's successor.
        A token re-registered under another principal moves to it."""
        with self._locked():
            data = self._load()
            cur = data.get(fields["token"])
            if cur and int(cur.get("rev", -1)) > fields["rev"]:
                return False
            data[fields["token"]] = {**fields, "principal": str(principal_id),
                                     "updated_at": time.time()}
            self._save(data)
            return True

    def remove(self, principal_id: str, token, rev) -> bool:
        """Forget. Applies only to the caller's own token, and only when `rev` is at least the
        stored one (rule 2). Returns whether a record was removed."""
        tok, rev = _token(token), _rev(rev)
        with self._locked():
            data = self._load()
            cur = data.get(tok)
            if not cur or cur.get("principal") != str(principal_id):
                return False
            if rev < int(cur.get("rev", 0)):
                return False
            del data[tok]
            self._save(data)
            return True

    def remove_unregistered(self, token) -> bool:
        """APNs said 410 Unregistered: the app is gone from that device. No rev; APNs is the
        authority on whether the token is still valid."""
        tok = _token(token)
        with self._locked():
            data = self._load()
            if data.pop(tok, None) is None:
                return False
            self._save(data)
            return True

    def live(self, device_alive) -> list[dict]:
        """Every token whose principal is still alive, pruning the rest (rule 3).
        `device_alive(device_id) -> bool`; the fleet bearer's tokens are alive while it is."""
        with self._locked():
            data = self._load()
            keep, dead = [], []
            for tok, rec in data.items():
                pid = rec.get("principal")
                if pid == LEGACY_PRINCIPAL or (pid and device_alive(pid)):
                    keep.append(rec)
                else:
                    dead.append(tok)
            if dead:
                for tok in dead:
                    data.pop(tok, None)
                self._save(data)
            return keep
