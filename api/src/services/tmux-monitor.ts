import { execFileSync } from 'child_process';

interface TmuxSession {
  name: string;
  created: string;
}

export function getTmuxSessions(): TmuxSession[] {
  try {
    const raw = execFileSync('tmux', ['list-sessions', '-F', '#{session_name}|#{session_created}'], {
      timeout: 5000,
      encoding: 'utf-8',
    });
    return raw
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        const [name, epoch] = line.split('|');
        return {
          name: name || '',
          created: epoch ? new Date(parseInt(epoch, 10) * 1000).toISOString() : '',
        };
      });
  } catch {
    // tmux not running or no sessions
    return [];
  }
}

export function getTmuxSessionNames(): Set<string> {
  const sessions = getTmuxSessions();
  return new Set(sessions.map((s) => s.name));
}

export function isAgentAlive(sessionName: string): boolean {
  try {
    execFileSync('tmux', ['has-session', '-t', sessionName], {
      timeout: 3000,
      encoding: 'utf-8',
    });
    return true;
  } catch {
    return false;
  }
}
