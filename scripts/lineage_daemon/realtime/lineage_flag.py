"""Durable per-lineage clean/flagged flag — the READ contract (court-scrub
riders 1 + 2). Provider-blind: keyed ONLY on lineage_root (every runtime has
one; rotations rename seats but the lineage_root is constant).

This is a READ-ONLY denylist consumer. The WRITE side (the existing court
detection path setting a lineage flagged) is OUT of B1 scope — B1 only reads and
disposes. The store shape (documented, versioned):

    { "schema": "lineage-flags/v1",
      "flagged": { "<lineage_root>": { "reason": "...", "ts": ... }, ... } }

FAIL-CLOSED (rider 2), load-bearing:
  * store missing / unreadable / not JSON / wrong schema  -> readable=False.
    The caller MUST block (never stream) on readable=False — until the detection
    path creates + maintains the denylist, EVERYTHING blocks (the safe INERT
    default).
  * store readable + well-formed:
      lineage in `flagged`        -> (flagged=True,  readable=True)  => block
      lineage NOT in `flagged`    -> (flagged=False, readable=True)  => clean
    (cleanliness is ~the whole fleet in practice; the denylist marks the rare
    flagged lineages — an absent ENTRY in a READABLE store is confirmed clean,
    distinct from an absent/corrupt STORE which is fail-closed.)
"""
import json
import os

SCHEMA = "lineage-flags/v1"


class LineageFlagStore:
    def __init__(self, flags_path):
        self._path = flags_path

    def _load(self):
        """Return the parsed denylist dict, or None on any read/schema failure
        (=> fail-closed)."""
        try:
            with open(self._path) as fh:
                obj = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
            return None
        flagged = obj.get("flagged")
        if not isinstance(flagged, dict):
            return None
        return flagged

    def status(self, lineage_root):
        """Return (flagged: bool, readable: bool). readable=False => fail-closed
        (caller blocks)."""
        flagged = self._load()
        if flagged is None:
            return (False, False)          # fail-closed: unreadable/absent-schema
        return (lineage_root in flagged, True)
