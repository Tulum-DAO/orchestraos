"""Process-instance fence — immutable live-process identity for rotation/recovery.

Piece 1 of the mechanical-rotation commission. Binds authority to a specific OS
process INSTANCE, not just a PID. The identity is a triple:

    (pid, starttime, boot_id)

  * ``pid``       — the OS process id.
  * ``starttime`` — the process start time in clock ticks since boot, from field
    22 of ``/proc/<pid>/stat``. Immutable for the life of a process; a reused pid
    gets a NEW starttime, so this defeats PID reuse *within* a boot.
  * ``boot_id``   — ``/proc/sys/kernel/random/boot_id``. starttime resets across
    reboots, so pinning the boot defeats reuse *across* reboots.

Safety contract — FAIL CLOSED. Every observation that cannot POSITIVELY confirm
"the same live process instance" resolves to not-alive. The fence never fabricates
liveness: an unreadable/absent ``/proc`` entry, a starttime mismatch, or a boot_id
mismatch all mean ``verify_alive == False``. ``capture_instance`` REFUSES a pid
that is not a live, readable process (you cannot fence something that isn't there).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone


class ProcessFenceError(RuntimeError):
    """Raised when an instance identity cannot be captured (no live/readable
    process). Fail-closed: refuse rather than invent an identity."""


def current_boot_id() -> str:
    """The current kernel boot id (stable for the life of the boot). Empty string
    if unreadable — callers treat an empty boot_id as unresolvable (fail closed)."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _read_starttime(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat (start time in clock ticks since boot), or
    None if the process is gone/unreadable. The comm field (2) may contain spaces
    and parens, so split AFTER the final ')'."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError:
        return None
    try:
        rparen = data.rindex(")")
    except ValueError:
        return None
    # after "pid (comm) " the remaining fields start at field 3 (state); field 22
    # (starttime) is therefore index 22 - 3 == 19 in the post-comm split.
    fields = data[rparen + 2:].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


class _RealReader:
    """Default proc reader — reads the live /proc. Tests inject a fake reader
    (codex §6) so pid-reuse / boot-change / dead cases need no real spoofing."""

    def starttime(self, pid: int) -> int | None:
        return _read_starttime(pid)

    def boot_id(self) -> str:
        return current_boot_id()


def _reader(reader):
    return reader if reader is not None else _RealReader()


@dataclass(frozen=True)
class InstanceIdentity:
    """An immutable (pid, starttime, boot_id) process-instance identity."""

    pid: int
    starttime: int
    boot_id: str

    def to_dict(self) -> dict:
        return {"pid": self.pid, "starttime": self.starttime,
                "boot_id": self.boot_id}

    @classmethod
    def from_dict(cls, d: dict) -> "InstanceIdentity":
        return cls(pid=int(d["pid"]), starttime=int(d["starttime"]),
                   boot_id=str(d["boot_id"]))


def capture_instance(pid: int, *, reader=None) -> InstanceIdentity:
    """Capture the immutable identity of a LIVE process. Fail-closed: raises
    ``ProcessFenceError`` if the pid names no readable live process, or the boot
    id is unresolvable — never returns a half-formed identity."""
    rd = _reader(reader)
    starttime = rd.starttime(pid)
    if starttime is None:
        raise ProcessFenceError(
            f"cannot capture process-instance for pid={pid}: no live/readable "
            f"/proc/{pid}/stat")
    boot_id = rd.boot_id()
    if not boot_id:
        raise ProcessFenceError(
            "cannot capture process-instance: boot_id unresolvable")
    return InstanceIdentity(pid=pid, starttime=starttime, boot_id=boot_id)


def verify_alive(ident: InstanceIdentity, *, reader=None) -> bool:
    """True iff the recorded instance is STILL the live process — same pid, same
    starttime, same boot. Any mismatch or unreadable /proc => False (fail closed)."""
    rd = _reader(reader)
    if not ident.boot_id or ident.boot_id != rd.boot_id():
        return False
    starttime = rd.starttime(ident.pid)
    if starttime is None:
        return False
    return starttime == ident.starttime


def is_dead(ident: InstanceIdentity, *, reader=None) -> bool:
    """True iff the recorded instance is provably GONE. The retirement
    postcondition (piece 2): a park_idle exit-0 is not proof — a retirement is
    only sound once the immutable predecessor instance ``is_dead``."""
    return not verify_alive(ident, reader=reader)


def same_instance(a: InstanceIdentity, b: InstanceIdentity) -> bool:
    """True iff two identities name the same process instance (all three legs)."""
    return (a.pid == b.pid and a.starttime == b.starttime
            and a.boot_id == b.boot_id)


# --- full seat-bound instance record (codex §1) ------------------------------

# Canonical NUL separator for the instance_id hash — a byte that cannot appear in
# any field value, so field boundaries are unambiguous (no concatenation collision).
_ID_SEP = "\x00"


def _derive_instance_id(*, boot_id: str, pid: int, starttime: int,
                        provider: str, provider_sid: str | None) -> str:
    """instance_id = sha256(boot_id || pid || proc_start_ticks || provider ||
    provider_sid) per codex §1. Derived ONLY from the identity legs — the movable
    tmux/host binding fields never enter the hash."""
    material = _ID_SEP.join([
        boot_id, str(pid), str(starttime), provider, provider_sid or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InstanceRecord:
    """The full seat-bound instance record (codex §1). The OS-process identity
    core (pid, starttime, boot_id) is bound to the intended provider seat via
    provider/provider_sid + the exact tmux pane/session + host. ``instance_id``
    derives from the identity legs only; the tmux/host fields are movable context."""

    pid: int
    starttime: int
    boot_id: str
    provider: str
    provider_sid: str | None
    tmux_session: str
    tmux_pane_id: str
    host_id: str
    observed_at: str

    @property
    def identity(self) -> InstanceIdentity:
        return InstanceIdentity(pid=self.pid, starttime=self.starttime,
                                boot_id=self.boot_id)

    @property
    def instance_id(self) -> str:
        return _derive_instance_id(
            boot_id=self.boot_id, pid=self.pid, starttime=self.starttime,
            provider=self.provider, provider_sid=self.provider_sid)

    @property
    def sid_exception(self) -> bool:
        """True when provider_sid is absent — codex §1 allows this only for a
        provider with no stable SID, and it must block hard arming for that seat."""
        return self.provider_sid is None

    def to_dict(self) -> dict:
        d = {"pid": self.pid, "starttime": self.starttime,
             "boot_id": self.boot_id, "provider": self.provider,
             "provider_sid": self.provider_sid,
             "tmux_session": self.tmux_session,
             "tmux_pane_id": self.tmux_pane_id, "host_id": self.host_id,
             "observed_at": self.observed_at}
        d["instance_id"] = self.instance_id  # denormalized for durable receipts
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InstanceRecord":
        sid = d.get("provider_sid")
        return cls(
            pid=int(d["pid"]), starttime=int(d["starttime"]),
            boot_id=str(d["boot_id"]), provider=str(d["provider"]),
            provider_sid=None if sid is None else str(sid),
            tmux_session=str(d["tmux_session"]),
            tmux_pane_id=str(d["tmux_pane_id"]), host_id=str(d["host_id"]),
            observed_at=str(d["observed_at"]))


def capture_record(pid: int, *, provider: str, provider_sid: str | None,
                   tmux_session: str, tmux_pane_id: str, host_id: str,
                   observed_at: str | None = None, reader=None) -> InstanceRecord:
    """Capture a full seat-bound instance record for a LIVE process. Fail-closed
    via ``capture_instance`` (refuses a non-live pid / unresolvable boot)."""
    ident = capture_instance(pid, reader=reader)
    ts = observed_at or datetime.now(timezone.utc).isoformat()
    return InstanceRecord(
        pid=ident.pid, starttime=ident.starttime, boot_id=ident.boot_id,
        provider=provider, provider_sid=provider_sid,
        tmux_session=tmux_session, tmux_pane_id=tmux_pane_id, host_id=host_id,
        observed_at=ts)
