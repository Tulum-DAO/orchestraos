"""RED tests for the durable per-lineage clean/flagged flag (court-scrub rider 1
+ 2). Provider-blind (keyed on lineage_root, which EVERY runtime has). Read
contract only — the WRITE (the existing court detection path) is out of B1 scope.

Fail-CLOSED is the load-bearing property: an unreadable / absent-schema flag
store => treat as NOT-readable => the caller blocks (never streams). A present,
well-formed denylist with a lineage ABSENT from it = that lineage is confirmed
clean (cleanliness is ~the whole fleet in practice — the denylist marks the rare
flagged ones)."""
import json
import os
import tempfile

from lineage_daemon.realtime.lineage_flag import LineageFlagStore


def _write(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh)


def test_present_denylist_flagged_lineage_is_flagged_and_readable():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lineage_flags.json")
        _write(p, {"schema": "lineage-flags/v1",
                   "flagged": {"root-abc": {"reason": "court"}}})
        st = LineageFlagStore(p)
        flagged, readable = st.status("root-abc")
        assert readable is True and flagged is True


def test_present_denylist_absent_lineage_is_clean():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lineage_flags.json")
        _write(p, {"schema": "lineage-flags/v1", "flagged": {}})
        st = LineageFlagStore(p)
        flagged, readable = st.status("root-clean")
        assert readable is True and flagged is False


def test_missing_file_fails_closed_not_readable():
    st = LineageFlagStore("/nonexistent/lineage_flags.json")
    flagged, readable = st.status("anything")
    assert readable is False, "absent store => fail-closed (caller must block)"


def test_corrupt_json_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lineage_flags.json")
        open(p, "w").write("{ this is not json")
        flagged, readable = LineageFlagStore(p).status("x")
        assert readable is False


def test_wrong_schema_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lineage_flags.json")
        _write(p, {"unexpected": "shape"})   # no schema / flagged keys
        flagged, readable = LineageFlagStore(p).status("x")
        assert readable is False


def test_provider_blind_keyed_on_lineage_root_only():
    # the same lineage_root resolves identically regardless of runtime — no
    # provider argument exists on the API (provider-blind by construction).
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lineage_flags.json")
        _write(p, {"schema": "lineage-flags/v1",
                   "flagged": {"r1": {"reason": "court"}}})
        st = LineageFlagStore(p)
        assert st.status("r1")[0] is True
        assert st.status("r2")[0] is False
