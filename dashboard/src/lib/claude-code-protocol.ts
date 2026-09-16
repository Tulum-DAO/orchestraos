/**
 * Claude Code Protocol Translator
 *
 * Stateful parser that converts raw tmux terminal output from Claude Code
 * sessions into structured events for the ChatView to render.
 *
 * This is a state machine, NOT an LLM. It uses regex pattern matching on
 * known Claude Code output formats.
 */

// Strip ANSI escape codes
export function stripAnsi(s: string): string {
  return s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '');
}

// === Agent States ===
export type AgentState =
  | 'idle'
  | 'working'
  | 'tool_approval'
  | 'numbered_options'
  | 'yes_no'
  | 'expandable'
  | 'error'
  | 'complete';

// === Protocol Events ===
export type ProtocolEvent =
  | UserMessageEvent
  | AssistantMessageEvent
  | ToolCallEvent
  | ToolRequestEvent
  | OptionsEvent
  | ExpandableEvent
  | StatusEvent
  | CompletionEvent
  | ErrorEvent;

export interface UserMessageEvent {
  type: 'user_message';
  content: string;
  id: string;
}

export interface AssistantMessageEvent {
  type: 'assistant_message';
  content: string;
  id: string;
}

export interface ToolCallEvent {
  type: 'tool_call';
  tool: string;
  command: string;
  results: string[];
  expandable?: string;
  id: string;
}

export interface ToolRequestEvent {
  type: 'tool_request';
  tool: string;
  description: string;
  details: string;
  actions: { label: string; keystroke: string; style: 'primary' | 'danger' | 'secondary' }[];
  id: string;
}

export interface OptionsEvent {
  type: 'options';
  options: { label: string; value: string }[];
  id: string;
}

export interface ExpandableEvent {
  type: 'expandable';
  summary: string;
  id: string;
}

export interface StatusEvent {
  type: 'status';
  status: 'working' | 'idle';
  detail?: string;
  id: string;
}

export interface CompletionEvent {
  type: 'completion';
  summary: string;
  id: string;
}

export interface ErrorEvent {
  type: 'error';
  message: string;
  id: string;
}

// Known Claude Code tool names
const TOOL_NAMES = [
  'Read', 'Edit', 'Write', 'Bash', 'Glob', 'Grep', 'Agent',
  'WebSearch', 'WebFetch', 'NotebookEdit', 'Skill', 'TaskCreate',
  'TaskUpdate', 'TaskList', 'TaskGet', 'ToolSearch',
];

// Patterns
const SPINNER = /^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏✻]/;
const USER_PROMPT = /^❯\s+(.+)/;
const IDLE_PROMPT = /^❯\s*$/;
const TOOL_CALL = new RegExp(`^[⏺●]?\\s*(${TOOL_NAMES.join('|')})\\(`);
const NUMBERED_OPTION = /^(?:(\d+)\.\s+|\((\d+)\)\s+|(\d+)\)\s+)(.+)/;
const EXPAND_HINT = /(.+?)\s*\(ctrl\+o\s+to\s+(expand|see all)\)/i;
const COMPLETION_MARKERS = /[✓✅]|\b(Done|Complete|committed|Created|Updated)\b/i;
const ERROR_MARKERS = /^(Error|✗|FAIL|\w+Error:)/i;
const PERMISSION_LINE = /\b(Allow|Deny|Always allow|Allow always)\b/;
const YES_NO_LINE = /\(Y\/n\)|\(y\/N\)|\[Y\/n\]|\[y\/N\]|yes\/no/i;
const AGENT_REPLY_START = /^[⏺●✻⎿]/;
const RESULT_LINE = /^[└⎿]\s*/;
const TABLE_BORDER = /^[├┤┌┐└┘┬┴│┼─━═╌╍┄┅┈┉|+\-:\s]+$/;
const STATUS_DETAIL = /^[✻*]\s*(Cooked|Brewed|Baked|Took|Crunched|Worked)\s+/i;
const COST_LINE = /^\$[\d.]+\s+/;

let _eventCounter = 0;
function nextId(): string {
  return `evt_${++_eventCounter}`;
}

export class ClaudeCodeProtocol {
  private state: AgentState = 'idle';
  private buffer: string[] = [];
  private lastEventSignatures = new Set<string>();

  getState(): AgentState {
    return this.state;
  }

  reset(): void {
    this.buffer = [];
    this.state = 'idle';
    this.lastEventSignatures.clear();
  }

  parse(cleanedLines: string[]): ProtocolEvent[] {
    this.buffer = cleanedLines;
    const events: ProtocolEvent[] = [];
    this.parseConversation(events);

    const newSigs = new Set<string>();
    const fresh = events.filter(e => {
      const sig = this.eventSignature(e);
      newSigs.add(sig);
      return !this.lastEventSignatures.has(sig);
    });
    this.lastEventSignatures = newSigs;
    this.updateState();
    return fresh;
  }

  private eventSignature(e: ProtocolEvent): string {
    switch (e.type) {
      case 'tool_call': return `tool_call:${e.tool}:${e.command}`;
      case 'tool_request': return `tool_request:${e.tool}:${e.description}`;
      case 'user_message':
      case 'assistant_message': return `${e.type}:${e.content}`;
      case 'options': return `options:${e.options.map(o => o.value).join(',')}`;
      case 'expandable': return `expandable:${e.summary}`;
      case 'status': return `status:${e.detail || e.status}`;
      case 'completion': return `completion:${e.summary}`;
      case 'error': return `error:${e.message}`;
    }
  }

  private updateState(): void {
    const tail = this.buffer.slice(-5);
    const lastLine = tail[tail.length - 1]?.trim() || '';

    if (IDLE_PROMPT.test(lastLine)) {
      this.state = 'idle';
    } else if (SPINNER.test(lastLine)) {
      this.state = 'working';
    } else if (PERMISSION_LINE.test(tail.join(' '))) {
      this.state = 'tool_approval';
    } else if (EXPAND_HINT.test(tail.join(' '))) {
      this.state = 'expandable';
    } else if (YES_NO_LINE.test(tail.join(' '))) {
      this.state = 'yes_no';
    }
  }

  private parseConversation(events: ProtocolEvent[]): void {
    const lines = this.buffer;
    let i = 0;

    while (i < lines.length) {
      const line = lines[i].trim();
      if (!line) { i++; continue; }

      // --- User message ---
      const userMatch = line.match(USER_PROMPT);
      if (userMatch) {
        const userLines = [userMatch[1]];
        i++;
        while (i < lines.length) {
          const next = lines[i].trim();
          if (!next || AGENT_REPLY_START.test(next) || TOOL_CALL.test(next) || SPINNER.test(next) || IDLE_PROMPT.test(next)) break;
          userLines.push(next);
          i++;
        }
        events.push({ type: 'user_message', content: userLines.join('\n'), id: nextId() });
        continue;
      }

      // --- Tool call ---
      const toolMatch = line.match(TOOL_CALL);
      if (toolMatch) {
        const toolName = toolMatch[1];
        const commandLine = line;
        i++;
        const results: string[] = [];
        let expandable: string | undefined;
        let hasPermission = false;

        while (i < lines.length) {
          const next = lines[i].trim();
          if (!next) { i++; continue; }
          if (USER_PROMPT.test(next) || TOOL_CALL.test(next) || IDLE_PROMPT.test(next)) break;
          if (AGENT_REPLY_START.test(next) && !RESULT_LINE.test(next)) break;

          if (PERMISSION_LINE.test(next)) {
            hasPermission = true;
            const permLines = [commandLine, ...results.map(r => `└ ${r}`), next];
            i++;
            while (i < lines.length) {
              const pn = lines[i].trim();
              if (!pn || USER_PROMPT.test(pn) || TOOL_CALL.test(pn)) break;
              permLines.push(pn);
              i++;
            }
            events.push({
              type: 'tool_request', tool: toolName, description: commandLine,
              details: permLines.join('\n'),
              actions: [
                { label: 'Allow', keystroke: 'y', style: 'primary' },
                { label: 'Deny', keystroke: 'n', style: 'danger' },
                { label: 'Always allow', keystroke: 'a', style: 'secondary' },
              ],
              id: nextId(),
            });
            break;
          }

          const expMatch = next.match(EXPAND_HINT);
          if (expMatch) { expandable = expMatch[1].trim(); i++; continue; }
          if (SPINNER.test(next)) break;

          const resultText = next.replace(RESULT_LINE, '');
          results.push(resultText || next);
          i++;
        }

        if (!hasPermission) {
          events.push({ type: 'tool_call', tool: toolName, command: commandLine, results, expandable, id: nextId() });
        }
        continue;
      }

      // --- Expandable (standalone) — also catch ^O Expand: variants ---
      const expandMatch = line.match(EXPAND_HINT);
      if (expandMatch) {
        events.push({ type: 'expandable', summary: expandMatch[1].trim(), id: nextId() });
        i++; continue;
      }
      // Filter out raw ^O expand hints that didn't match the regex
      if (/ctrl\+o to expand|⎿.*\(ctrl\+o/i.test(line) || /^\^O\s*Expand/i.test(line) || /\+\d+\s+lines?\s*\(ctrl/i.test(line)) {
        i++; continue;
      }

      // --- Status detail ---
      if (STATUS_DETAIL.test(line)) {
        events.push({ type: 'status', status: 'working', detail: line.replace(/^[✻*]\s*/, ''), id: nextId() });
        i++; continue;
      }

      // --- Cost line ---
      if (COST_LINE.test(line)) {
        events.push({ type: 'status', status: 'idle', detail: line, id: nextId() });
        i++; continue;
      }

      // --- Spinner ---
      if (SPINNER.test(line)) {
        while (i < lines.length && SPINNER.test(lines[i].trim())) i++;
        const spinnerText = line.replace(SPINNER, '').trim();
        events.push({ type: 'status', status: 'working', detail: spinnerText || 'Working...', id: nextId() });
        continue;
      }

      // --- Idle prompt ---
      if (IDLE_PROMPT.test(line)) { i++; continue; }

      // --- Numbered options (only at tail) ---
      const optMatch = line.match(NUMBERED_OPTION);
      if (optMatch) {
        const opts: { label: string; value: string }[] = [];
        const seen = new Set<string>();
        let j = i;
        while (j < lines.length) {
          const optLine = lines[j].trim();
          const m = optLine.match(NUMBERED_OPTION);
          if (m) {
            const num = m[1] || m[2] || m[3];
            const text = m[4].trim();
            if (!seen.has(num)) { seen.add(num); opts.push({ label: text, value: num }); }
            j++;
          } else { break; }
        }
        if (opts.length >= 2) {
          const tail = lines.slice(j);
          const hasPromptAfter = tail.some(l => IDLE_PROMPT.test(l.trim()));
          const hasSpinnerAfter = tail.some(l => SPINNER.test(l.trim()));
          const hasTextAfter = tail.some(l => { const t = l.trim(); return t && !IDLE_PROMPT.test(t) && !SPINNER.test(t); });
          if (!hasPromptAfter && !hasSpinnerAfter && !hasTextAfter) {
            events.push({ type: 'options', options: opts, id: nextId() });
            i = j; continue;
          }
        }
      }

      // --- Completion ---
      if (COMPLETION_MARKERS.test(line) && !RESULT_LINE.test(line)) {
        events.push({ type: 'completion', summary: line, id: nextId() });
        i++; continue;
      }

      // --- Error ---
      if (ERROR_MARKERS.test(line) || /\bError:\s/.test(line)) {
        events.push({ type: 'error', message: line, id: nextId() });
        i++; continue;
      }

      // --- Assistant message (catch-all) ---
      const msgLines: string[] = [line];
      i++;
      while (i < lines.length) {
        const next = lines[i].trim();
        if (!next) { i++; continue; }
        if (USER_PROMPT.test(next) || TOOL_CALL.test(next) || SPINNER.test(next) ||
            IDLE_PROMPT.test(next) || EXPAND_HINT.test(next) || STATUS_DETAIL.test(next)) break;
        if (ERROR_MARKERS.test(next) || COMPLETION_MARKERS.test(next)) break;
        if (TABLE_BORDER.test(next)) { msgLines.push(next); i++; continue; }
        if (RESULT_LINE.test(next) && !msgLines.some(l => RESULT_LINE.test(l.trim()))) break;
        msgLines.push(next);
        i++;
      }
      events.push({ type: 'assistant_message', content: msgLines.join('\n'), id: nextId() });
    }
  }
}
