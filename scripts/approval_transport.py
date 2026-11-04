"""Flag-gated notification transport dispatcher.

The ONE seam for Phase-2 cutover. Today `approval.py` calls
`approval_notify.notify` directly (ntfy). When the operator is ready, the cutover is a
one-line import swap in approval.py:

    from approval_notify import notify   ->   from approval_transport import notify

This module then routes by APPROVAL_TRANSPORT (default "ntfy" = unchanged):
  ntfy : legacy ntfy only
  apns : native APNs only (raises if not ready — use to force/verify the native path)
  auto : APNs when ready + a device token exists, else FALL BACK to ntfy (recommended)

Because the default is "ntfy" and approval.py is NOT yet swapped, importing or
deploying this changes nothing on the live loop.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apns_config import APPROVAL_TRANSPORT
from approval_schema import ApprovalStore


def notify(rid: str, store: ApprovalStore | None = None):
    store = store or ApprovalStore()
    mode = APPROVAL_TRANSPORT
    # Settings › Notifications, before ANY transport (ntfy, APNs, the fallback): a card the person does not want now is
    # stamped and not sent (approval_notify._policy_gate; ntfy's own notify() asks again, which is harmless).
    row = store.get(rid)
    if row and row.get("status") == "pending":
        from approval_notify import _policy_gate
        import notify_policy
        if _policy_gate("approval", row, store) != notify_policy.OK:
            return None

    if mode == "ntfy":
        from approval_notify import notify as ntfy_notify
        return ntfy_notify(rid, store=store)

    if mode in ("apns", "auto"):
        from apns_notify import notify as apns_notify, ApnsNotReady
        try:
            return apns_notify(rid, store=store)
        except ApnsNotReady:
            if mode == "auto":
                from approval_notify import notify as ntfy_notify
                print(f"[transport] apns not ready — falling back to ntfy for {rid}", file=sys.stderr)
                return ntfy_notify(rid, store=store)
            raise

    # unknown value -> safest default (never drop a push)
    from approval_notify import notify as ntfy_notify
    print(f"[transport] unknown APPROVAL_TRANSPORT={mode!r}; using ntfy", file=sys.stderr)
    return ntfy_notify(rid, store=store)
