"""Push-notification device tokens, as registered by the paired apps.

One JSON file (<data dir>/state/push-tokens.json, 0600), keyed by the APNs token. Each record
says WHO registered it (the caller's principal: a device id, or "legacy:<bearer fingerprint>"
for the shared fleet bearer), for WHICH app (bundle_id is the APNs topic, and the Watch is a
different app from the phone) and for WHICH APNs host (env: an App Store / TestFlight build
registers a production token, an Xcode build a sandbox one; one global setting breaks a mixed
fleet).

The rules this module exists to keep:

1. NO DEFAULTS. platform, bundle_id and env are required. With the shared fleet bearer, platform
   is the only thing telling the phone, iPad and watch apart, and a wrong topic or host is a
   push APNs rejects.
2. THE CLIENT'S `rev` ORDERS EVERYTHING. An APNs token belongs to the app install, not to the
   pairing, so "Forget, then pair again without reinstalling" yields the SAME token. The client
   sends a strictly increasing rev (ms since epoch) on EVERY PUT and DELETE. A PUT at or below
   the stored rev changes nothing; a DELETE below it changes nothing; a DELETE leaves a
   tombstone, so a delayed older PUT cannot bring a forgotten device back.
3. A TOKEN BELONGS TO ITS OWNER. Another principal can take it over only when the owner is
   positively gone (revoked, or a rotated fleet bearer) or the owner is the fleet bearer (a
   phone moving off the shared bearer onto its own token).
4. TOKENS DIE WITH THEIR OWNER, ON POSITIVE EVIDENCE ONLY. A sender reads `live()`, which sends
   only to owners that are positively alive and prunes only owners that are positively dead.
   An owner we cannot read (store unreadable, file missing, wrong data dir) is "unknown": not
   sent to, and NOT pruned. Missing data never deletes anything.
5. BOUNDED. Each principal holds at most MAX_PER_DEVICE tokens (MAX_PER_LEGACY for the fleet
   bearer, which carries phone + iPad + watch in two envs); the oldest is evicted, so a
   reinstalled app never gets stuck.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("push_tokens")

PLATFORMS = ("iphone", "ipad", "watch")
ENVS = ("sandbox", "production")
#: The published app's two topics. An install that ships its own build overrides this with
#: ORCHESTRA_PUSH_BUNDLE_IDS. It is an allowlist so a client cannot make the sender push under
#: an arbitrary topic.
DEFAULT_BUNDLE_IDS = ("co.bizypro.OrchestraOS", "co.bizypro.OrchestraOS.watchkitapp")
#: Prefix of the fleet bearer's principal ("legacy:<fingerprint of the bearer>").
LEGACY_PREFIX = "legacy:"
#: rev is ms since epoch, so anything more than a day ahead of now is refused: a far-future rev
#: would let one write pin a token (or a Forget) for good.
REV_FUTURE_SLACK_MS = 24 * 3600 * 1000
MAX_PER_DEVICE = 8
MAX_PER_LEGACY = 16
#: Forgets remembered per principal; the oldest go first. Bounds a PUT/DELETE loop.
MAX_TOMBSTONES = 32
#: How long a Forget is remembered, so a delayed older PUT cannot resurrect the token.
TOMBSTONE_TTL_S = 30 * 24 * 3600

ALIVE, DEAD, UNKNOWN = "alive", "dead", "unknown"

_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64,200}$")


class PushTokenError(ValueError):
    """A registration that is missing a field or names a value we do not accept."""


def allowed_bundle_ids() -> tuple[str, ...]:
    raw = os.environ.get("ORCHESTRA_PUSH_BUNDLE_IDS", "")
    got = tuple(b.strip() for b in raw.split(",") if b.strip())
    return got or DEFAULT_BUNDLE_IDS


def _token(value) -> str:
    tok = str(value or "").strip()
    if not _TOKEN_RE.match(tok):
        raise PushTokenError("token must be the APNs device token as hex (64+ characters)")
    return tok.lower()


def _rev(value) -> int:
    ceiling = int(time.time() * 1000) + REV_FUTURE_SLACK_MS
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
        raise PushTokenError("rev must be ms since epoch (not in the future), growing on every request")
    return value


def _stored_rev(rec) -> int:
    try:
        return int(rec.get("rev", 0))
    except (TypeError, ValueError):
        return 0


def legacy_principal(bearer: str) -> str:
    """The push principal for a fleet bearer: a short fingerprint, never the bearer itself."""
    return LEGACY_PREFIX + hashlib.sha256(bearer.encode("utf-8")).hexdigest()[:16]


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


def _is_legacy(principal_id) -> bool:
    return str(principal_id or "").startswith(LEGACY_PREFIX)


class PushTokenStore:
    def __init__(self, path):
        self.path = Path(path)
        self.retired_path = self.path.with_name(self.path.stem + ".retired-bearers.json")

    # ------------------------------------------------------------------ file plumbing

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
        """The records. Only a MISSING file is empty. A file we cannot parse is moved aside
        (kept for recovery) and logged, never silently overwritten with one new record."""
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except ValueError as e:
            aside = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            os.replace(self.path, aside)
            log.warning(f"push tokens: {self.path} was unreadable ({e}); moved to {aside}, "
                        "starting empty (apps re-register on their next PUT)")
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.unlink()            # a leftover tmp would keep its own (possibly wider) mode
        except FileNotFoundError:
            pass
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    @staticmethod
    def _expire_tombstones(data: dict, now: float) -> None:
        for tok in [t for t, r in data.items()
                    if r.get("deleted") and now - float(r.get("deleted_at") or 0) > TOMBSTONE_TTL_S]:
            del data[tok]

    @staticmethod
    def _evict_oldest(data: dict, principal_id: str, deleted: bool, cap: int, stamp: str) -> None:
        mine = sorted((r.get(stamp) or 0, t) for t, r in data.items()
                      if bool(r.get("deleted")) == deleted and r.get("principal") == principal_id)
        for _, tok in mine[:max(0, len(mine) - cap)]:
            del data[tok]

    def _enforce_cap(self, data: dict, principal_id: str) -> None:
        cap = MAX_PER_LEGACY if _is_legacy(principal_id) else MAX_PER_DEVICE
        self._evict_oldest(data, principal_id, False, cap, "updated_at")
        self._evict_oldest(data, principal_id, True, MAX_TOMBSTONES, "deleted_at")

    # ------------------------------------------------------------------ fleet bearer rotation

    def retire_bearer(self, bearer: str) -> None:
        """Record that this fleet bearer was rotated away. This is the POSITIVE evidence that
        makes its tokens dead; a sender that merely sees a different bearer (another HOME,
        another token file) treats them as unknown and leaves them alone."""
        with self._locked():
            got = self.retired_fingerprints()
            got.add(legacy_principal(bearer))
            tmp = self.retired_path.with_suffix(".tmp")
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(sorted(got), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.retired_path)

    def retired_fingerprints(self) -> set:
        try:
            raw = self.retired_path.read_text()
        except FileNotFoundError:
            return set()
        except OSError as e:
            log.warning(f"push tokens: {self.retired_path} unreadable ({e}); no bearer counts as retired")
            return set()
        try:
            got = json.loads(raw)
            if not isinstance(got, list):
                raise ValueError("not a list")
        except ValueError as e:
            aside = self.retired_path.with_name(f"{self.retired_path.name}.corrupt-{int(time.time())}")
            try:
                os.replace(self.retired_path, aside)
            except OSError:
                pass
            log.warning(f"push tokens: {self.retired_path} was unreadable ({e}); moved to {aside}")
            return set()
        return {str(x) for x in got}

    # ------------------------------------------------------------------ operations

    def register(self, principal_id: str, fields: dict, classify) -> str | None:
        """Store or refresh a token. Returns None when stored, else why not: "stale_rev" (a newer
        request is on file), "forgotten" (a Forget at or after this rev) or "owned" (another
        device that is still paired, or one we cannot read, holds it). `classify(principal) ->
        ALIVE | DEAD | UNKNOWN` decides whether another principal's token may be taken over."""
        principal_id = str(principal_id)
        now = time.time()
        with self._locked():
            data = self._load()
            self._expire_tombstones(data, now)
            cur = data.get(fields["token"])
            rev = fields["rev"]
            if cur and cur.get("deleted"):
                if rev <= _stored_rev(cur):
                    return "forgotten"      # a Forget at or after this rev (rule 2)
            elif cur:
                owner, stored = cur.get("principal"), _stored_rev(cur)
                # Ownership first: a caller who can't take the token over learns only "owned",
                # never how its rev compares (which would date the owner's last registration).
                if owner != principal_id and not _is_legacy(owner) and classify(owner) != DEAD:
                    return "owned"          # someone else's token (rule 3)
                if rev < stored or (owner != principal_id and rev == stored):
                    return "stale_rev"      # older, or a takeover without a newer rev
            data[fields["token"]] = {**fields, "principal": principal_id, "updated_at": now}
            self._enforce_cap(data, principal_id)
            self._save(data)
            return None

    def remove(self, principal_id: str, token, rev) -> bool:
        """Forget. Applies only to the caller's own token, and only when `rev` is at least the
        stored one. Leaves a tombstone at `rev` (rule 2). Returns whether a record was removed."""
        tok, rev = _token(token), _rev(rev)
        now = time.time()
        with self._locked():
            data = self._load()
            self._expire_tombstones(data, now)
            cur = data.get(tok)
            if not cur or cur.get("deleted") or cur.get("principal") != str(principal_id):
                return False
            if rev < _stored_rev(cur):
                return False
            data[tok] = {"deleted": True, "rev": rev, "deleted_at": now,
                         "principal": str(principal_id)}
            self._enforce_cap(data, str(principal_id))
            self._save(data)
            return True

    def remove_unregistered(self, token) -> bool:
        """APNs said 410 Unregistered: the app is gone from that device. No rev; APNs is the
        authority on whether the token is still valid."""
        tok = _token(token)
        with self._locked():
            data = self._load()
            cur = data.get(tok)
            if not cur or cur.get("deleted"):
                return False
            del data[tok]
            self._save(data)
            return True

    def live(self, classify) -> list[dict]:
        """The tokens to send to: owners positively ALIVE. Owners positively DEAD are pruned;
        UNKNOWN owners are kept and skipped (rule 4)."""
        now = time.time()
        with self._locked():
            data = self._load()
            before = len(data)
            self._expire_tombstones(data, now)
            keep, dead = [], []
            verdicts: dict = {}
            for tok, rec in data.items():
                if rec.get("deleted"):
                    continue
                pid = rec.get("principal")
                if pid not in verdicts:
                    verdicts[pid] = classify(pid) if pid else UNKNOWN
                if verdicts[pid] == ALIVE:
                    keep.append(rec)
                elif verdicts[pid] == DEAD:
                    dead.append(tok)
            for tok in dead:
                del data[tok]
            if dead or len(data) != before:
                self._save(data)
            return keep
