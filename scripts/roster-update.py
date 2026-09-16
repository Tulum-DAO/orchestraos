#!/usr/bin/env python3
"""
roster-update.py — Write/update an agent's entry in state/live-roster.json.
Called by spawn-agent.sh / agent-reincarnator.sh / manual resume at launch time.

Usage:
  python3 scripts/roster-update.py <agent_name> <session_id> <cwd> [--model MODEL]
  python3 scripts/roster-update.py --reconcile   # tmux ls → mark dead, discover new

The roster is the single source of truth for crash recovery:
  "what was running, which session ID, which cwd, which model."
"""
import json, os, sys, subprocess
from datetime import datetime, timezone

ROSTER = os.path.join(os.path.dirname(__file__), '..', 'state', 'live-roster.json')
ROSTER = os.path.abspath(ROSTER)

def load():
    if os.path.exists(ROSTER):
        with open(ROSTER) as f:
            return json.load(f)
    return {}

def save(data):
    os.makedirs(os.path.dirname(ROSTER), exist_ok=True)
    with open(ROSTER, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def upsert(name, sid, cwd, model='opus'):
    r = load()
    r[name] = {
        'session_id': sid,
        'tmux_session': name,
        'cwd': cwd,
        'model': model,
        'launched_at': datetime.now(timezone.utc).isoformat(),
        'status': 'alive',
    }
    save(r)
    print(f'roster: {name} → {sid} (cwd {cwd}, model {model})')

def _descendants(root_pid):
    """root_pid + every descendant pid, from one /proc scan (no ps dependency)."""
    children = {}
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/stat') as f:
                ppid = f.read().split(')')[-1].split()[1]
            children.setdefault(ppid, []).append(pid)
        except OSError:
            continue
    out, queue = [], [str(root_pid)]
    while queue:
        pid = queue.pop()
        out.append(pid)
        queue.extend(children.get(pid, []))
    return out


def sid_for_pane(pane_pid, projdir):
    """POSITIVE sid attribution only (mis-attribution bug, 2026-08-18).

    The old predicate — newest jsonl by mtime in the project dir — cross-attributed
    a co-located agent's sid whenever cwds are shared (which is the normal case in
    ~/scripts/agent-orchestra): the roster carried the SUPERVISOR's sid on two
    supervised agents' records. The roster is crash-recovery truth, so a wrong sid
    resumes someone else's session into a crashed pane.

    The ONLY direct proof: a descendant holds <projdir>/<sid>.jsonl open.
    argv --resume is a HINT, not proof (gm msg_2d2e43e3: it is a LAUNCH-TIME
    string, and stale-resume_command respawns are a live event class — 4 on
    2026-08-18 alone); see argv_resume_hint + the reconcile confirmation loop,
    which requires the claimed transcript's mtime to ADVANCE while the pane
    lives — the fd path's proof standard, lazily. Otherwise return '' — an
    empty sid is a visible gap; a wrong one is a silent cross-wire.
    """
    projdir = os.path.realpath(projdir)
    for pid in _descendants(pane_pid):
        try:
            for fd in os.listdir(f'/proc/{pid}/fd'):
                try:
                    target = os.readlink(f'/proc/{pid}/fd/{fd}')
                except OSError:
                    continue
                if target.endswith('.jsonl') and os.path.dirname(
                        os.path.realpath(target)) == projdir:
                    return os.path.basename(target)[:-6]
        except OSError:
            pass
    return ''


def argv_resume_hint(pane_pid):
    """The sid the pane's process tree CLAIMS via --resume argv. A claim, not
    an attribution — it must be confirmed by transcript-mtime advance."""
    for pid in _descendants(pane_pid):
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = f.read().decode(errors='replace').split('\0')
            if '--resume' in argv:
                i = argv.index('--resume')
                if i + 1 < len(argv) and argv[i + 1]:
                    return argv[i + 1]
        except OSError:
            pass
    return ''


def _pane_pid(sess):
    try:
        return subprocess.check_output(
            ['tmux', 'display-message', '-t', sess, '-p', '#{pane_pid}'],
            text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return ''


def _projdir_for(cwd):
    slug = cwd.replace('/', '-').lstrip('-')
    return os.path.expanduser(f'~/.claude/projects/-{slug}')


def reconcile():
    """tmux ls → mark dead agents, discover untracked ones."""
    r = load()
    # get live tmux sessions
    try:
        out = subprocess.check_output(['tmux', 'ls'], text=True, stderr=subprocess.DEVNULL)
        live = {line.split(':')[0] for line in out.strip().splitlines()}
    except subprocess.CalledProcessError:
        live = set()

    changed = False
    # mark dead
    for name, entry in r.items():
        was = entry.get('status')
        now = 'alive' if name in live else 'dead'
        if was != now:
            entry['status'] = now
            if now == 'dead':
                entry['died_at'] = datetime.now(timezone.utc).isoformat()
            changed = True

    # discover untracked live sessions (not in roster, not infra)
    infra = {'combo-proxy','custom-llm','dashboard','lwe-feedback','telegram-router','gm','jarvis-gm','orchestra-builder'}
    for sess in live - set(r.keys()) - infra:
        pane_pid = _pane_pid(sess)
        try:
            cwd = os.readlink(f'/proc/{pane_pid}/cwd') if pane_pid and os.path.exists(f'/proc/{pane_pid}/cwd') else ''
        except OSError:
            cwd = ''
        sid = ''
        if cwd and pane_pid:
            projdir = _projdir_for(cwd)
            if os.path.isdir(projdir):
                # positive attribution only — never newest-mtime in a SHARED
                # project dir (that predicate cross-attributed supervisor sids
                # to supervised panes, 2026-08-18). Empty beats wrong.
                sid = sid_for_pane(pane_pid, projdir)
        r[sess] = {
            'session_id': sid,
            'tmux_session': sess,
            'cwd': cwd,
            'model': 'unknown',
            'launched_at': '',
            'status': 'alive',
            'discovered': True,
        }
        changed = True

    # retry attribution on alive DISCOVERED records still lacking a proven sid:
    # fd proof only fires when the agent is caught mid-write, and reconcile
    # runs on a cron — the empty state is self-healing, never guessed.
    for name, entry in r.items():
        if (entry.get('discovered') and entry.get('status') == 'alive'
                and not entry.get('session_id') and entry.get('cwd')):
            pid = _pane_pid(name)
            projdir = _projdir_for(entry['cwd'])
            if not (pid and os.path.isdir(projdir)):
                continue
            sid = sid_for_pane(pid, projdir)
            if sid:
                entry['session_id'] = sid
                entry['sid_proof'] = 'open-fd'
                changed = True
                continue
            # argv-hint path: confirm the CLAIM only when its transcript's
            # mtime has ADVANCED while this pane lives (gm msg_2d2e43e3 —
            # a launch-time string is not identity; progress under the claim is)
            claimed = entry.get('claimed_sid')
            if claimed:
                tpath = os.path.join(projdir, f'{claimed}.jsonl')
                try:
                    mtime = os.path.getmtime(tpath)
                except OSError:
                    continue
                seen = entry.get('claimed_sid_seen_mtime')
                if seen is not None and mtime > seen:
                    entry['session_id'] = claimed
                    entry['sid_proof'] = 'argv+mtime-advance'
                    changed = True
                elif seen is None:
                    entry['claimed_sid_seen_mtime'] = mtime
                    changed = True
            else:
                hint = argv_resume_hint(pid)
                if hint:
                    entry['claimed_sid'] = hint
                    tpath = os.path.join(projdir, f'{hint}.jsonl')
                    try:
                        entry['claimed_sid_seen_mtime'] = os.path.getmtime(tpath)
                    except OSError:
                        entry['claimed_sid_seen_mtime'] = None
                    changed = True

    if changed:
        save(r)
    alive = sum(1 for e in r.values() if e.get('status') == 'alive')
    dead = sum(1 for e in r.values() if e.get('status') == 'dead')
    print(f'roster reconciled: {alive} alive, {dead} dead, {len(live)} tmux sessions')

if __name__ == '__main__':
    if '--reconcile' in sys.argv:
        reconcile()
    elif len(sys.argv) >= 4:
        model = 'opus'
        if '--model' in sys.argv:
            mi = sys.argv.index('--model')
            model = sys.argv[mi+1] if mi+1 < len(sys.argv) else 'opus'
        upsert(sys.argv[1], sys.argv[2], sys.argv[3], model)
    else:
        print('Usage: roster-update.py <name> <sid> <cwd> [--model M]')
        print('       roster-update.py --reconcile')
        sys.exit(1)
