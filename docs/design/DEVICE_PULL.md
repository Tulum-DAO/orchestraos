# Device pull: when is a seat "remote" (5 lines + the test)

Context: branch feat/device-pull (3b49fa83) holds the device-copy code gm split out of #601/#602. Its gate was "seat runs
here vs remote transcript readable here"; today nothing reads remote transcripts, so the code is dormant. This note fixes
the rule before any PR opens.

1. A seat is LOCAL when this host has a live tmux session for it OR its transcript resolves on this host
   (resolveTranscriptPath); the registry `machine` label never overrides either (the repo's precedence rule already).
2. A seat is REMOTE only when (a) its `machine` names a registry machine that is not this host (isThisHost false),
   (b) neither local signal exists, and (c) the transcript the chat is showing came from a REMOTE READ (a new, explicit
   `transcript_origin: {machine}` stamp on the transcript envelope set by whatever future path reads it over SSH).
3. Without (c) there is no device pull: a remote label with no remote-read stamp is "unknown", and unknown = local
   (no SSH, no copy), which is what #602's fail-safe already does.
4. A pulled file lands only under state/agents/<id>/device/ (existing allowed root); the remote path must be under the
   seat's cwd on that machine; same SSH identity as services/cross-machine.ts; no new credential.
5. Doctor row `media:device-pull` says dormant / active (which machine) / refused (why).

Test that pins it: a seat labelled "mac" with a local transcript -> local, images stamped, fake ssh never called; the
same seat with transcript_origin=mac and no local signals -> remote, pull scheduled into device/; label "mac" with no
transcript at all -> unknown, nothing happens.

## Review (orchestra-builder, 2026-11-23): rules OK; MUST be in the PR
6. transcript_origin is set ONLY by the server's own remote-read code path: never from a request body, query param,
   transcript content or client envelope. Test: a client-sent transcript_origin=mac leaves the seat local, ssh never called.
7. Remote confinement after resolution: realpath on the remote, refuse "..", refuse a symlink escaping cwd, regular files
   only, size cap, only media types the chat renders. Tests: traversal, symlink escape, oversize, directory.
8. No remote shell string: read with `ssh <host> -- cat -- <shlex-quoted path>` (or sftp), argv lists only, never a local
   shell string. Test: a name with `$(...)` and a space is fetched literally or refused, never executed.
9. Local landing: sanitised basename, never overwrite (suffix), landed path under state/agents/<id>/device/ after resolve.
Nice to have: doctor row counts refusals by reason since boot; per-seat rate cap on pulls.
