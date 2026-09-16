"""verify_capture — re-check a captured WAL against its source transcript BY
EFFECT. This is the soak PROOF INSTRUMENT: it does not trust the tailer, it
re-derives the truth from the source and the stored integrity hashes.

Checks:
  - contiguous: seq runs 1..N with no holes;
  - integrity: for each event whose source_path is the transcript and whose
    source_off is set, re-read that source line and confirm sha256 == stored
    integrity (multi-item lines share one line hash);
  - unparseable: count of adapter 'unparseable-line' markers (should be 0);
  - cursor: the resume cursor sits on a newline boundary within the file.

Read-only. No mutation of anything.
"""
import hashlib
import os


def _line_at(fh, off):
    fh.seek(off)
    raw = fh.readline()
    return raw.rstrip(b"\n").rstrip(b"\r")


def verify_capture(store, transcript_path, lineage_root=None):
    events = store.events(lineage_root)
    report = {
        "events": len(events),
        "max_seq": store.max_seq(),
        "contiguous": True,
        "integrity_ok": 0,
        "integrity_total": 0,
        "unparseable": 0,
        "cursor_ok": False,
        "cursor_last_off": None,
        "kinds_sample": [r["kind"] for r in events[:12]],
    }

    # contiguity 1..N
    seqs = [r["seq"] for r in events]
    if seqs and seqs != list(range(seqs[0], seqs[0] + len(seqs))):
        report["contiguous"] = False

    # integrity re-hash against the source transcript
    try:
        fh = open(transcript_path, "rb")
    except OSError:
        fh = None
    try:
        for r in events:
            if r["summary"] and "unparseable" in r["summary"]:
                report["unparseable"] += 1
            if (fh is not None and r["source_path"] == transcript_path
                    and r["source_off"] is not None and r["integrity"]):
                report["integrity_total"] += 1
                got = hashlib.sha256(_line_at(fh, r["source_off"])).hexdigest()
                if got == r["integrity"]:
                    report["integrity_ok"] += 1
    finally:
        if fh is not None:
            fh.close()

    # cursor sits on a newline boundary (byte before last_off is \n, or off 0/EOF)
    cur = store.get_cursor(transcript_path)
    if cur is not None:
        off = cur["last_off"]
        report["cursor_last_off"] = off
        try:
            size = os.path.getsize(transcript_path)
            if off == 0:
                report["cursor_ok"] = True
            elif off <= size:
                with open(transcript_path, "rb") as f2:
                    f2.seek(off - 1)
                    report["cursor_ok"] = (f2.read(1) == b"\n")
        except OSError:
            report["cursor_ok"] = False

    report["ok"] = bool(
        report["contiguous"]
        and report["unparseable"] == 0
        and report["cursor_ok"]
        and report["integrity_total"] > 0
        and report["integrity_ok"] == report["integrity_total"])
    return report
