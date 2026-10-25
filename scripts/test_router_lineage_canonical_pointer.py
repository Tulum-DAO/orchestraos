"""Lineage-root fallback in the router: alias mail after a consolidation, the DB canonical
pointer (name first, then session-id confirmation), the DB-free ungated-member filter, and the
`canonical-not-live` hold. Scratch ORCHESTRA_DIR and scratch registry DBs only; nothing here
touches tmux.

After a consolidation the successor's session is renamed to the bare root, but an alias's entry
in agent-sessions.json still names its renamed-away session; the root's own entry carries
lineage_root=None. Without counting the root as a member of its own lineage, alias mail resolved
to `no-live-head` and was retried forever without alarming."""
import importlib.util
import os
import pytest
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('message_router_alias', os.path.join(_here, 'message-router.py'))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


@pytest.fixture(autouse=True)
def _sandbox_orchestra_dir(tmp_path, monkeypatch):
    sandbox = tmp_path / 'orchestra'
    (sandbox / 'state').mkdir(parents=True)
    monkeypatch.setenv('ORCHESTRA_DIR', str(sandbox))
    monkeypatch.setattr(mr, 'ORCHESTRA_DIR', sandbox, raising=False)
    assert str(getattr(mr, 'ORCHESTRA_DIR')) == str(sandbox), 'fence not applied'
    return sandbox


ROOT = 'alpha'


ALIAS = 'alpha-g72'


def _consolidated_meta(**over):
    meta = {ROOT: {'tmux_session': ROOT, 'lineage_root': None, 'status': 'online', 'succeeded_by': None}, ALIAS: {'tmux_session': ALIAS, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}}
    meta.update(over)
    return meta


def test_mail_to_a_current_alias_reaches_the_roots_live_pane():
    sess, fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, _consolidated_meta())
    assert sess == ROOT, f'alias mail did not reach the live root: {(sess, fwd, reason)}'
    assert reason != 'no-live-head'


def test_mail_to_the_root_itself_is_unaffected():
    sess, fwd, reason = mr.resolve_delivery_target(ROOT, {ROOT}, _consolidated_meta())
    assert (sess, fwd, reason) == (ROOT, None, 'direct-live')


def test_alias_of_a_DEAD_lineage_stays_queued_and_is_not_misrouted():
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, set(), _consolidated_meta())
    assert sess is None, 'mail was delivered somewhere while the whole lineage was dead'
    assert reason == 'no-live-head'


def test_alias_is_never_routed_to_an_unrelated_live_seat():
    meta = _consolidated_meta()
    meta['beta'] = {'tmux_session': 'beta', 'lineage_root': 'beta', 'status': 'online', 'succeeded_by': None}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {'beta'}, meta)
    assert sess != 'beta', "the alias's mail was routed to a seat in a DIFFERENT lineage — membership is not scoped to the lineage"
    assert sess is None and reason == 'no-live-head', (sess, reason)


def test_lineage_scoping_holds_even_when_the_root_is_live():
    meta = _consolidated_meta()
    meta['beta'] = {'tmux_session': 'beta', 'lineage_root': 'beta', 'status': 'online', 'succeeded_by': None}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, 'beta'}, meta)
    assert sess == ROOT, f'an unrelated live seat perturbed an otherwise unambiguous lineage: {(sess, reason)}'


def test_a_live_alias_session_still_wins_directly():
    sess, fwd, reason = mr.resolve_delivery_target(ALIAS, {ALIAS, ROOT}, _consolidated_meta())
    assert (sess, fwd, reason) == (ALIAS, None, 'direct-live')


def test_provisioning_successor_still_does_not_steal_the_lane():
    meta = {'pred': {'tmux_session': 'pred', 'lineage_root': 'pred', 'succeeded_by': 'pred-g2', 'status': 'online'}, 'pred-g2': {'tmux_session': 'pred-g2', 'lineage_root': 'pred', 'status': 'provisioning'}}
    sess, fwd, reason = mr.resolve_delivery_target('pred', {'pred', 'pred-g2'}, meta)
    assert (sess, fwd, reason) == ('pred', None, 'direct-live')


def test_provisioning_successor_with_dead_predecessor_is_retryable_not_delivered():
    meta = {'pred': {'tmux_session': 'pred', 'lineage_root': 'pred', 'succeeded_by': 'pred-g2', 'status': 'online'}, 'pred-g2': {'tmux_session': 'pred-g2', 'lineage_root': 'pred', 'status': 'provisioning'}}
    sess, _fwd, reason = mr.resolve_delivery_target('pred', {'pred-g2'}, meta)
    assert sess is None and reason == 'successor-ungated'


def test_root_counts_as_a_member_of_its_own_lineage():
    sess, _fwd, reason = mr._lineage_root_resolve(ALIAS, _consolidated_meta()[ALIAS], {ROOT}, _consolidated_meta())
    assert sess == ROOT, f'the root was not counted as a member of its own lineage: {reason}'


def test_ambiguity_is_still_fail_loud_not_a_guess():
    meta = _consolidated_meta()
    meta['alpha-g71'] = {'tmux_session': 'alpha-g71', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, 'alpha-g71'}, meta)
    assert sess is None and reason == 'ambiguous-lineage', (sess, reason)


def test_unknown_address_is_still_unknown():
    sess, _fwd, reason = mr.resolve_delivery_target('not-an-agent', {ROOT}, _consolidated_meta())
    assert sess is None and reason == 'unknown-address'


def test_membership_is_by_lineage_NOT_by_name_prefix():
    meta = _consolidated_meta()
    meta['alpha-v2'] = {'tmux_session': 'alpha-v2', 'lineage_root': 'alpha-v2', 'status': 'online', 'succeeded_by': None}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {'alpha-v2'}, meta)
    assert sess != 'alpha-v2', "membership matched a NAME PREFIX and routed this lineage's mail into a different lineage — the explicitly documented name-stem hazard"
    assert sess is None and reason == 'no-live-head', (sess, reason)


def test_prefix_sibling_does_not_create_false_ambiguity():
    meta = _consolidated_meta()
    meta['alpha-v2'] = {'tmux_session': 'alpha-v2', 'lineage_root': 'alpha-v2', 'status': 'online', 'succeeded_by': None}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, 'alpha-v2'}, meta)
    assert sess == ROOT, (sess, reason)
import sqlite3


GREEN = 'alpha-g73'


RETIRED = 'alpha-g71'


def _write_canonical(sandbox, root, tmux_session):
    db = sandbox / 'state' / 'orchestra-registry.db'
    con = sqlite3.connect(str(db))
    con.execute('create table if not exists canonical (root text primary key, generation_id integer, tmux_session text, status text)')
    con.execute('insert or replace into canonical values (?,?,?,?)', (root, 1, tmux_session, 'online'))
    con.commit()
    con.close()
    return db


def _prewarm_meta(green_status='provisioning'):
    return {ROOT: {'tmux_session': ROOT, 'lineage_root': None, 'status': 'online', 'succeeded_by': None}, ALIAS: {'tmux_session': ALIAS, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}, RETIRED: {'tmux_session': RETIRED, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}, GREEN: {'tmux_session': GREEN, 'lineage_root': ROOT, 'status': green_status, 'succeeded_by': None}}


@pytest.mark.parametrize('green_status', ['provisioning', 'online'])


@pytest.mark.parametrize('addr', [ALIAS, RETIRED])
def test_alias_mail_does_not_stall_while_a_green_is_live(_sandbox_orchestra_dir, addr, green_status):
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    sess, _fwd, reason = mr.resolve_delivery_target(addr, {ROOT, GREEN}, _prewarm_meta(green_status))
    assert sess == ROOT, f'alias mail stalled during prewarm ({addr}, green={green_status}): {(sess, reason)}'


def test_mid_swap_canonical_pointing_AT_the_alias_session_wins(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _prewarm_meta('online'))
    assert sess == GREEN, f'the DB canonical pointer was ignored in favour of the root: {(sess, reason)}'


def test_tiebreak_stays_LOUD_when_the_db_is_unreadable(_sandbox_orchestra_dir):
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _prewarm_meta('online'))
    assert sess is None and reason == 'ambiguous-lineage', (sess, reason)


def test_tiebreak_stays_LOUD_when_canonical_names_no_live_candidate(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, 'some-third-session')
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _prewarm_meta('online'))
    assert sess is None, f'a winner was invented: {(sess, reason)}'
    assert reason == 'ambiguous-lineage', reason
    assert reason in mr.TELEGRAM_ALERT_REASONS, 'the hold is silent'


def test_the_direct_live_path_does_not_read_the_db(_sandbox_orchestra_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(mr, '_registry_db_path', lambda: (calls.append(1), '/nonexistent')[1], raising=False)
    sess, fwd, reason = mr.resolve_delivery_target(ROOT, {ROOT}, _consolidated_meta())
    assert (sess, fwd, reason) == (ROOT, None, 'direct-live')
    assert calls == [], 'the DB was read on the direct-live hot path'
    sess, _f, reason = mr.resolve_delivery_target(ALIAS, {ALIAS, ROOT}, _consolidated_meta())
    assert (sess, reason) == (ALIAS, 'direct-live')
    assert calls == [], 'the DB was read on the direct-live hot path (alias form)'


def test_a_row_queued_before_the_fix_resolves_after_it(_sandbox_orchestra_dir):
    meta, sessions = (_consolidated_meta(), {ROOT})
    pre_fix_members = [a for a, e in meta.items() if e.get('lineage_root') == ROOT and e.get('tmux_session', a) in sessions]
    assert pre_fix_members == [], 'pre-fix reproduction is wrong; it found a live member'
    sess, _fwd, _r = mr.resolve_delivery_target(ALIAS, sessions, meta)
    assert sess == ROOT, 'the same queued row still does not resolve'


def _mid_gate_meta(green_status='provisioning'):
    return {ROOT: {'tmux_session': ROOT, 'lineage_root': None, 'status': 'online', 'succeeded_by': GREEN}, ALIAS: {'tmux_session': ALIAS, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}, GREEN: {'tmux_session': GREEN, 'lineage_root': ROOT, 'status': green_status, 'succeeded_by': None}}


def test_fallback_never_delivers_to_a_provisioning_green_with_the_DB_UNREADABLE(_sandbox_orchestra_dir):
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _mid_gate_meta())
    assert sess != GREEN, f'mail was delivered to a PROVISIONING green on the fallback path: {(sess, reason)}'
    assert sess == ROOT, f"the live predecessor did not get its own lane's mail: {(sess, reason)}"


def test_the_provisioning_guard_does_not_need_the_db_to_be_readable():
    import unittest.mock as _m
    with _m.patch.object(mr, '_registry_db_path', lambda: '/nonexistent/registry.db'):
        sess, _fwd, _r = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _mid_gate_meta())
    assert sess == ROOT, 'the provisioning exclusion depended on a readable DB'


def test_provisioning_green_live_but_root_DEAD_stays_queued(_sandbox_orchestra_dir):
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {GREEN}, _mid_gate_meta())
    assert sess is None, f'mail reached the provisioning green once the root died: {sess}'
    assert reason == 'successor-ungated', reason
    assert reason in mr.TELEGRAM_ALERT_REASONS, 'nobody owns the lane and nothing will alarm'


def test_a_canonical_pointer_BEATS_a_stale_provisioning_projection(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, GREEN)
    meta = _mid_gate_meta()
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, meta)
    assert sess == GREEN, f'a stale `provisioning` projection vetoed the canonical pointer and the mail went to the retiring blue: {(sess, reason)}'
    assert reason == 'lineage-root-canonical', reason


def test_pointer_is_consulted_BEFORE_member_counting(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, GREEN)
    meta = _mid_gate_meta('online')
    meta[ROOT]['succeeded_by'] = None
    meta[GREEN]['succeeded_by'] = 'alpha-g74'
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, meta)
    assert sess == GREEN, f'member counting won over the canonical pointer on the fallback path: {(sess, reason)}'
    assert reason == 'lineage-root-canonical', reason


def test_split_brain_with_an_unreadable_db_is_still_LOUD(_sandbox_orchestra_dir):
    meta = _mid_gate_meta('online')
    meta[ROOT]['succeeded_by'] = None
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, meta)
    assert sess is None and reason == 'ambiguous-lineage', (sess, reason)


def test_alert_text_names_a_warming_green_instead_of_blaming_the_config():
    topo = mr._describe_topology(ALIAS, {ROOT, GREEN}, _mid_gate_meta())
    assert 'broken' not in topo.lower(), topo
    assert GREEN in topo, f'the topology line does not name the green it saw: {topo}'
    assert 'provisioning' in topo.lower() or 'warming' in topo.lower(), topo


def test_alert_text_still_describes_a_genuine_split_brain():
    meta = _mid_gate_meta('online')
    meta[ROOT]['succeeded_by'] = None
    topo = mr._describe_topology(ALIAS, {ROOT, GREEN}, meta)
    assert ROOT in topo and GREEN in topo, topo
    assert '2' in topo or 'two' in topo.lower(), f'the line does not say how many: {topo}'


def test_row_8_a_SPAWNING_green_does_not_win_alias_mail_with_the_db_unreadable(_sandbox_orchestra_dir):
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _mid_gate_meta('spawning'))
    assert sess != GREEN, f"a SPAWNING green won the lane's mail: {(sess, reason)}"
    assert sess == ROOT, (sess, reason)


def test_row_8b_a_SPAWNING_green_with_canonical_on_the_root(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    sess, _fwd, _r = mr.resolve_delivery_target(ALIAS, {ROOT, GREEN}, _mid_gate_meta('spawning'))
    assert sess == ROOT, sess


def test_the_two_ungated_status_lists_are_one_object():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(mr))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op, cmp_ in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if not isinstance(cmp_, (ast.Tuple, ast.List, ast.Set)):
                continue
            vals = {e.value for e in cmp_.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if {'provisioning', 'spawning'} & vals:
                offenders.append(getattr(node, 'lineno', '?'))
    assert not offenders, f'an inline ungated-status literal is back in the router at line(s) {offenders}; use _UNGATED_STATUSES so the fallback filter and the dispatch exemption cannot drift'
    assert mr._UNGATED_STATUSES == frozenset({'provisioning', 'spawning'})


S_GREEN = '9d6a8052-green-green-green-000000000000'


S_BLUE = '1edb6149-blue-blue-blue-0000000000000000'


def _write_canonical_with_sid(sandbox, root, tmux_session, sid, gen_id=1110):
    db = sandbox / 'state' / 'orchestra-registry.db'
    con = sqlite3.connect(str(db))
    con.execute('create table if not exists canonical (root text primary key, generation_id integer, tmux_session text, status text)')
    con.execute('create table if not exists generations (id integer primary key, root text, generation integer, session_id text)')
    con.execute('insert or replace into generations values (?,?,?,?)', (gen_id, root, 13, sid))
    con.execute('insert or replace into canonical values (?,?,?,?)', (root, gen_id, tmux_session, 'online'))
    con.commit()
    con.close()
    return db


def _swap_meta(root_sid, alias_sid=None, alias_status='online'):
    return {ROOT: {'tmux_session': ROOT, 'lineage_root': None, 'status': 'online', 'succeeded_by': None, 'session_id': root_sid}, ALIAS: {'tmux_session': ALIAS, 'lineage_root': ROOT, 'status': alias_status, 'succeeded_by': None, 'session_id': alias_sid}}


def test_case_b_renamed_green_with_a_current_projection_DELIVERS(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ALIAS, S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, _swap_meta(S_GREEN))
    assert sess == ROOT, f'the normal post-rename path stalled: {(sess, reason)}'
    assert reason == 'lineage-root-canonical-sid', reason


def test_case_a_dead_green_and_a_live_RETIRING_BLUE_HOLDS(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, GREEN, S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, _swap_meta(S_BLUE))
    assert sess is None, f'mail was delivered to the RETIRING BLUE while the canonical owner is down — delivered-but-unowned: {(sess, reason)}'
    assert reason == 'canonical-not-live', reason


def test_case_b_LAG_a_stale_projection_HOLDS_rather_than_misdelivering(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ALIAS, S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, _swap_meta(S_BLUE))
    assert sess is None and reason == 'canonical-not-live', (sess, reason)


def test_a_missing_sid_on_the_live_entry_is_a_ZERO_never_a_fall_through(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ALIAS, S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, _swap_meta(None))
    assert sess is None and reason == 'canonical-not-live', (sess, reason)


def test_a_sid_carried_only_by_a_DEAD_entry_is_a_ZERO(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ALIAS, S_GREEN)
    meta = _swap_meta(root_sid=S_BLUE, alias_sid=S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, meta)
    assert sess != ALIAS, 'mail was injected into a session that does not exist'
    assert sess is None and reason == 'canonical-not-live', (sess, reason)


def test_POSITIVE_CONTROL_same_topology_plus_a_LIVE_carrier_delivers_to_the_LIVE_one(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ALIAS, S_GREEN)
    meta = _swap_meta(root_sid=S_GREEN, alias_sid=S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, meta)
    assert sess == ROOT, f'did not deliver to the LIVE carrier of the canonical sid: {(sess, reason)}'


def test_two_LIVE_carriers_of_one_sid_is_a_split_brain_and_stays_LOUD(_sandbox_orchestra_dir):
    sibling = 'alpha-g74'
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, 'gone-session', S_GREEN)
    meta = _swap_meta(root_sid=S_GREEN)
    meta[sibling] = {'tmux_session': sibling, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': S_GREEN}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, sibling}, meta)
    assert sess is None and reason == 'ambiguous-lineage', (sess, reason)


def test_the_match_is_by_SID_not_by_name_stem(_sandbox_orchestra_dir):
    looks_like_gen13 = 'alpha-g13'
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, 'gone-session', S_GREEN)
    meta = _swap_meta(root_sid=S_BLUE)
    meta[looks_like_gen13] = {'tmux_session': looks_like_gen13, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': S_BLUE}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, looks_like_gen13}, meta)
    assert sess is None, f'delivered on a NAME resemblance to the canonical generation: {(sess, reason)}'
    assert reason == 'ambiguous-lineage', reason


def test_a_pointer_naming_a_LIVE_session_is_still_answered_by_NAME(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ALIAS, S_GREEN)
    meta = _swap_meta(root_sid=S_GREEN, alias_sid=S_GREEN)
    meta[RETIRED] = {'tmux_session': RETIRED, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': 'old-sid'}
    sess, _fwd, reason = mr.resolve_delivery_target(RETIRED, {ROOT, ALIAS}, meta)
    assert (sess, reason) != (None, 'ambiguous-lineage'), 'sid was tried BEFORE the name: two live carriers of the green sid went ambiguous on a path the 1:1 name match resolves'
    assert (sess, reason) == (ALIAS, 'lineage-root-canonical'), (sess, reason)


def test_canonical_not_live_PAGES_and_says_the_owner_is_down():
    assert 'canonical-not-live' in mr.TELEGRAM_ALERT_REASONS, 'a lane whose owner is down would be held SILENTLY forever'


def test_two_entries_ONE_live_session_is_ONE_candidate_by_sid(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, 'gone-session', S_GREEN)
    meta = _swap_meta(root_sid=S_GREEN)
    meta[ALIAS]['tmux_session'] = ROOT
    meta[ALIAS]['session_id'] = S_GREEN
    sess, _fwd, reason = mr.resolve_delivery_target(RETIRED, {ROOT}, {**meta, RETIRED: {'tmux_session': RETIRED, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': 'old'}})
    assert (sess, reason) == (ROOT, 'lineage-root-canonical-sid'), f'two entries naming ONE live session were counted as a split brain: {(sess, reason)}'


def test_two_entries_ONE_live_session_is_ONE_candidate_by_NAME(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, ROOT, S_GREEN)
    meta = _swap_meta(root_sid=S_GREEN)
    meta[ALIAS]['tmux_session'] = ROOT
    sess, _fwd, reason = mr.resolve_delivery_target(RETIRED, {ROOT}, {**meta, RETIRED: {'tmux_session': RETIRED, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': 'old'}})
    assert (sess, reason) == (ROOT, 'lineage-root-canonical'), (sess, reason)


def test_zero_carriers_with_SEVERAL_live_sessions_is_AMBIGUOUS_not_owner_down(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, 'retired-root-session', S_GREEN)
    meta = _swap_meta(root_sid=S_BLUE)
    meta['alpha-g74'] = {'tmux_session': 'alpha-g74', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': 'another-unconfirmed-sid'}
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT, 'alpha-g74'}, meta)
    assert sess is None, f'a winner was invented: {(sess, reason)}'
    assert reason == 'ambiguous-lineage', f'two live sessions were reported as an owner who is DOWN: {reason}'


def test_zero_carriers_with_ONE_live_session_is_still_owner_down(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, GREEN, S_GREEN)
    sess, _fwd, reason = mr.resolve_delivery_target(ALIAS, {ROOT}, _swap_meta(S_BLUE))
    assert (sess, reason) == (None, 'canonical-not-live'), (sess, reason)


def test_zero_carriers_and_TWO_ENTRIES_naming_ONE_live_session_is_owner_down(_sandbox_orchestra_dir):
    _write_canonical_with_sid(_sandbox_orchestra_dir, ROOT, GREEN, S_GREEN)
    meta = _swap_meta(root_sid=S_BLUE)
    meta[ALIAS]['tmux_session'] = ROOT
    meta[ALIAS]['session_id'] = S_BLUE
    sess, _fwd, reason = mr.resolve_delivery_target(RETIRED, {ROOT}, {**meta, RETIRED: {'tmux_session': RETIRED, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None, 'session_id': 'old'}})
    assert (sess, reason) == (None, 'canonical-not-live'), (sess, reason)
