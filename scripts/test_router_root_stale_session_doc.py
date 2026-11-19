"""A ROOT address whose own session doc still names the green's boot alias after the swap.

The swap renames the green `<root>-g<N>` tmux session to the bare root and then repoints
`canonical.tmux_session` to it, but the root's session doc in the identity DB keeps the alias
(DB-wins over the flat file in load_agent_meta). The root's entry carries lineage_root=None, so
the lineage fallback used to return `no-live-head` before it ever read the canonical pointer:
every pushed approval and inject to that seat failed while it sat idle. A root address now falls back to the canonical session when that
session is live. Scratch ORCHESTRA_DIR and scratch registry DBs only; nothing here touches tmux."""
import importlib.util
import os
import sqlite3

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('message_router_root_stale', os.path.join(_here, 'message-router.py'))
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
STALE = 'alpha-g45'      # the green's boot alias, renamed away by the swap


def _write_canonical(sandbox, root, tmux_session):
    con = sqlite3.connect(str(sandbox / 'state' / 'orchestra-registry.db'))
    con.execute('create table if not exists canonical (root text primary key, generation_id integer, tmux_session text, status text)')
    con.execute('insert or replace into canonical values (?,?,?,?)', (root, 1, tmux_session, 'online'))
    con.commit()
    con.close()


def _stale_meta(**over):
    """The shape load_agent_meta returned live: root doc and green alias doc both name the alias."""
    meta = {ROOT: {'tmux_session': STALE, 'lineage_root': None, 'status': 'online', 'succeeded_by': None},
            STALE: {'tmux_session': STALE, 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None},
            'alpha-gen44': {'tmux_session': None, 'lineage_root': ROOT, 'status': 'parked', 'succeeded_by': None}}
    meta.update(over)
    return meta


def test_root_with_a_stale_alias_doc_reaches_its_live_canonical_session(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    sess, fwd, reason = mr.resolve_delivery_target(ROOT, {ROOT, 'beta'}, _stale_meta())
    assert sess == ROOT, f'the live canonical session was never consulted: {(sess, fwd, reason)}'
    assert reason == 'lineage-root-canonical', reason


def test_root_whose_canonical_session_is_NOT_live_stays_no_live_head(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, {'beta'}, _stale_meta())
    assert (sess, reason) == (None, 'no-live-head'), (sess, reason)


def test_root_with_no_canonical_row_stays_no_live_head(_sandbox_orchestra_dir):
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, {ROOT}, _stale_meta())
    assert (sess, reason) == (None, 'no-live-head'), 'a live session was guessed without the pointer'


def test_root_with_an_unreadable_db_stays_no_live_head(_sandbox_orchestra_dir, monkeypatch):
    monkeypatch.setattr(mr, '_registry_db_path', lambda: '/nonexistent/registry.db', raising=False)
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, {ROOT}, _stale_meta())
    assert (sess, reason) == (None, 'no-live-head'), (sess, reason)


def test_canonical_naming_a_live_green_alias_mid_swap_wins_for_the_root(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, 'alpha-g46')
    meta = _stale_meta(**{'alpha-g46': {'tmux_session': 'alpha-g46', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}})
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, {'alpha-g46'}, meta)
    assert (sess, reason) == ('alpha-g46', 'lineage-root-canonical'), (sess, reason)


def test_a_non_root_alias_address_is_unchanged(_sandbox_orchestra_dir):
    """The fix is for ROOT addresses only: an alias whose own session is live still goes direct,
    and an alias whose session is gone resolves exactly as before."""
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    meta = _stale_meta()
    assert mr.resolve_delivery_target(STALE, {STALE, ROOT}, meta) == (STALE, None, 'direct-live')
    sess, _fwd, reason = mr.resolve_delivery_target(STALE, {ROOT}, meta)
    assert (sess, reason) == (None, 'no-live-head'), (sess, reason)


def test_a_leaf_with_no_lineage_and_no_canonical_row_is_unchanged(_sandbox_orchestra_dir):
    meta = {'solo': {'tmux_session': 'solo', 'lineage_root': None, 'status': 'online', 'succeeded_by': None}}
    assert mr.resolve_delivery_target('solo', {'solo'}, meta) == ('solo', None, 'direct-live')
    sess, _fwd, reason = mr.resolve_delivery_target('solo', {'beta'}, meta)
    assert (sess, reason) == (None, 'no-live-head'), (sess, reason)


def test_the_canonical_session_is_never_another_lineages_seat(_sandbox_orchestra_dir):
    """Only the root's OWN canonical row is read: a live seat of another lineage never wins."""
    _write_canonical(_sandbox_orchestra_dir, 'beta', 'beta')
    meta = _stale_meta(beta={'tmux_session': 'beta', 'lineage_root': None, 'status': 'online', 'succeeded_by': None})
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, {'beta'}, meta)
    assert (sess, reason) == (None, 'no-live-head'), (sess, reason)


def test_resolve_live_head_end_to_end_for_the_stale_root(_sandbox_orchestra_dir, monkeypatch):
    """The exact call approval_resume makes (lineage_resolve.resolve_live_head) with injected
    sessions/meta, so the approval path itself is covered, not only the router function."""
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    import lineage_resolve
    monkeypatch.setattr(lineage_resolve, '_router', lambda: mr)
    assert lineage_resolve.resolve_live_head(ROOT, sessions={ROOT}, meta=_stale_meta()) == (ROOT, 'lineage-root-canonical')


def test_a_live_old_alias_does_not_hide_the_live_canonical_session(_sandbox_orchestra_dir):
    """A retained predecessor alias that is still live must not turn the root's stale doc into a
    hold: the pointer names a live session, so it wins by name."""
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    meta = _stale_meta(**{'alpha-g44': {'tmux_session': 'alpha-g44', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}})
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, {ROOT, 'alpha-g44'}, meta)
    assert (sess, reason) == (ROOT, 'lineage-root-canonical'), (sess, reason)


def test_a_root_served_by_its_own_session_is_not_labelled_forwarded(_sandbox_orchestra_dir):
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    assert mr.resolve_delivery_target(ROOT, {ROOT}, _stale_meta()) == (ROOT, None, 'lineage-root-canonical')


@pytest.mark.parametrize('extra, live', [
    ({'alpha-g44': {'tmux_session': 'alpha-g44', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}}, {'alpha-g44'}),
    ({'alpha-g46': {'tmux_session': 'alpha-g46', 'lineage_root': ROOT, 'status': 'provisioning', 'succeeded_by': None}}, {'alpha-g46'}),
    ({'alpha-g44': {'tmux_session': 'alpha-g44', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None},
      'alpha-g43': {'tmux_session': 'alpha-g43', 'lineage_root': ROOT, 'status': 'online', 'succeeded_by': None}}, {'alpha-g44', 'alpha-g43'}),
])
@pytest.mark.parametrize('db', ['dead-pointer', 'no-db'])
def test_a_root_without_a_live_pointer_stays_a_silent_retry(_sandbox_orchestra_dir, extra, live, db):
    """No new outcomes (and no new pages) for a root address: a live old alias, a lone
    provisioning green or two live members must not turn into lineage-root / successor-ungated /
    ambiguous-lineage / canonical-not-live. Only a LIVE pointer changes the answer."""
    if db == 'dead-pointer':
        _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    sess, _fwd, reason = mr.resolve_delivery_target(ROOT, live, _stale_meta(**extra))
    assert (sess, reason) == (None, 'no-live-head'), (sess, reason)


def test_each_stale_root_is_logged_once_on_stderr_never_stdout(_sandbox_orchestra_dir, capsys, monkeypatch):
    """The counter that keeps stale root docs visible: one stderr line per root and session per
    process, nothing on stdout (the batch resolver CLI prints JSON there)."""
    monkeypatch.setattr(mr, '_STALE_ROOT_DOCS_SEEN', set())
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    for _ in range(3):
        mr.resolve_delivery_target(ROOT, {ROOT}, _stale_meta())
    out, err = capsys.readouterr()
    assert out == ''
    assert err.count(f'STALE-ROOT-DOC {ROOT}:') == 1, err


def test_a_direct_live_root_logs_nothing(_sandbox_orchestra_dir, capsys, monkeypatch):
    monkeypatch.setattr(mr, '_STALE_ROOT_DOCS_SEEN', set())
    _write_canonical(_sandbox_orchestra_dir, ROOT, ROOT)
    meta = _stale_meta(**{ROOT: {'tmux_session': ROOT, 'lineage_root': None, 'status': 'online', 'succeeded_by': None}})
    assert mr.resolve_delivery_target(ROOT, {ROOT}, meta) == (ROOT, None, 'direct-live')
    assert 'STALE-ROOT-DOC' not in capsys.readouterr().err
