# precall_snapshot.py — <1s, all-local, best-effort session-start context.
def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def build(page, gm_tail, approvals_count, fleet_line):
    tail = _safe(gm_tail, [])
    approvals = _safe(approvals_count, 0)
    fleet = _safe(fleet_line, "")
    lines = [
        f"[Voice session context] Current screen: {page or 'unknown'}.",
        f"Pending approvals: {approvals}.",
        f"Fleet: {fleet}." if fleet else "",
        "Recent gm conversation:" if tail else "",
    ]
    lines += [f"  {t}" for t in tail[-30:]]
    return "\n".join(l for l in lines if l)
