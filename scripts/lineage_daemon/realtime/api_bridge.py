#!/usr/bin/env python3
"""Build B — api_bridge: the thin CLI the TS telemetry API shells to REUSE B1's
court-scrub disposition seam WITHOUT re-implementing the fail-closed flag check.

It is a wrapper, not a second implementation: every safety decision is delegated
to B1's LineageFlagStore / token_extractor.disposition_for_stream. The TS route
calls this at stream-OPEN (and on a slow re-check) — the per-delta hot path is a
pure TS relay of the already-court-gated delta log.

Subcommands (each prints ONE line of JSON to stdout):
  flag-status  --lineage-root R --flags-path P
      -> {"flagged": bool, "readable": bool}   (readable=False => caller BLOCKS)
  disposition  --runtime R --lineage-root L --flags-path P --raw-file F
      -> token_extractor.disposition_for_stream(...) as JSON (block => text="")

BLOCKING-1 (claude COUNTER, DEC-1788461603): an EMPTY/missing lineage_root fails
CLOSED here (readable=False / block) BEFORE consulting the store — a bare
LineageFlagStore.status("") would otherwise read (clean, readable) and STREAM.

Fail-closed everywhere: unreadable/absent/corrupt store => block; any internal
error => the caller treats a non-zero exit / non-JSON as block (documented
contract with the TS side).
"""
import argparse
import json
import sys

from lineage_daemon.realtime.lineage_flag import LineageFlagStore
from lineage_daemon.realtime.token_extractor import disposition_for_stream


def _block_disposition(reason):
    return {"stream_mode": "block", "text": "", "streamed": False,
            "live_model_voice_tokens": False, "reason": reason}


def cmd_flag_status(args):
    # BLOCKING-1: empty/None lineage_root => fail-closed (never hit the store).
    if not args.lineage_root:
        return {"flagged": False, "readable": False}
    flagged, readable = LineageFlagStore(args.flags_path).status(args.lineage_root)
    return {"flagged": bool(flagged), "readable": bool(readable)}


def cmd_disposition(args):
    if not args.lineage_root:
        return _block_disposition("empty-lineage-root")     # BLOCKING-1
    try:
        with open(args.raw_file, "rb") as fh:
            raw = fh.read()
    except OSError:
        return _block_disposition("raw-unreadable")         # fail-closed
    # gemini model-voice is protobuf; normalize.render_body (via the reconcile
    # path) protobuf-decodes before any check. The LIVE pty path here treats raw
    # as text; the emission-time PIN blocks the whole flagged stream regardless
    # of body, so a flagged lineage never decodes/leaks. For a CLEAN lineage the
    # raw is already ANSI text (pty bytes), decoded permissively.
    if isinstance(raw, (bytes, bytearray)):
        text_in = raw.decode("utf-8", "replace")
    else:
        text_in = raw
    return disposition_for_stream(args.runtime, text_in,
                                  lineage_root=args.lineage_root,
                                  flag_store=LineageFlagStore(args.flags_path))


def main(argv=None):
    p = argparse.ArgumentParser(prog="api_bridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    fs = sub.add_parser("flag-status")
    fs.add_argument("--lineage-root", default="")
    fs.add_argument("--flags-path", required=True)
    fs.set_defaults(fn=cmd_flag_status)

    dp = sub.add_parser("disposition")
    dp.add_argument("--runtime", required=True)
    dp.add_argument("--lineage-root", default="")
    dp.add_argument("--flags-path", required=True)
    dp.add_argument("--raw-file", required=True)
    dp.set_defaults(fn=cmd_disposition)

    args = p.parse_args(argv)
    try:
        out = args.fn(args)
    except Exception as e:                      # fail-closed: never leak on error
        if args.cmd == "flag-status":
            out = {"flagged": False, "readable": False}
        else:
            out = _block_disposition(f"bridge-error:{type(e).__name__}")
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
