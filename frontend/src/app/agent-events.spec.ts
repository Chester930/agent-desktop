import { describe, expect, it } from 'vitest';

import { normalizeAgentEvents } from './agent-events';

describe('normalizeAgentEvents', () => {
  it('normalizes a canonical text delta', () => {
    expect(normalizeAgentEvents({ type: 'text_delta', text: 'hello', agent: 'coder' })).toEqual([
      { type: 'text_delta', text: 'hello', agent: 'coder' },
    ]);
  });

  it('converts legacy assistant text blocks into text deltas', () => {
    expect(
      normalizeAgentEvents({
        type: 'assistant',
        message: {
          content: [
            { type: 'text', text: 'one' },
            { type: 'tool_use', id: 't1', name: 'Bash', input: {} },
          ],
        },
      }),
    ).toEqual([
      { type: 'text_delta', text: 'one' },
      { type: 'tool_call_start', id: 't1', name: 'Bash', input: {} },
    ]);
  });

  it('converts legacy tool results and ignores malformed events', () => {
    expect(
      normalizeAgentEvents({
        type: 'user',
        message: { content: [{ type: 'tool_result', tool_use_id: 't1', content: 'ok' }] },
      }),
    ).toEqual([{ type: 'tool_call_end', id: 't1', output: 'ok', is_error: false }]);
    expect(normalizeAgentEvents({ type: 'text_delta', text: 42 })).toEqual([]);
    expect(normalizeAgentEvents(null)).toEqual([]);
  });

  it('normalizes permission requests and preserves completion cost metadata', () => {
    expect(
      normalizeAgentEvents({
        type: 'permission_request',
        request_id: 'r1',
        agent: 'builder',
        command: 'npm test',
      }),
    ).toEqual([
      { type: 'permission_requested', request_id: 'r1', agent: 'builder', command: 'npm test' },
    ]);
    expect(
      normalizeAgentEvents({ type: 'result', session_id: 's1', total_cost_usd: 0.42 }),
    ).toEqual([{ type: 'run_finished', session_id: 's1', cost_usd: 0.42 }]);
  });

  it('normalizes ACP-style tool_call/tool_call_update lifecycle events', () => {
    expect(
      normalizeAgentEvents({
        type: 'tool_call',
        id: 't1',
        name: 'Bash',
        input: { cmd: 'ls' },
        status: 'in_progress',
        agent: 'coder',
      }),
    ).toEqual([
      {
        type: 'tool_call_start',
        id: 't1',
        name: 'Bash',
        input: { cmd: 'ls' },
        agent: 'coder',
        status: 'in_progress',
      },
    ]);

    expect(
      normalizeAgentEvents({ type: 'tool_call_update', id: 't1', output: 'done', status: 'completed' }),
    ).toEqual([{ type: 'tool_call_end', id: 't1', output: 'done', is_error: false, status: 'completed' }]);

    expect(
      normalizeAgentEvents({ type: 'tool_call_update', id: 't1', output: 'boom', status: 'failed' }),
    ).toEqual([{ type: 'tool_call_end', id: 't1', output: 'boom', is_error: true, status: 'failed' }]);

    expect(normalizeAgentEvents({ type: 'tool_call', name: 'Bash' })).toEqual([]);
  });

  it('normalizes an ACP-style plan event and drops empty plans', () => {
    expect(
      normalizeAgentEvents({
        type: 'plan',
        agent: 'lead',
        steps: [{ content: 'read spec', status: 'completed' }, { content: 'write code' }],
      }),
    ).toEqual([
      {
        type: 'plan',
        agent: 'lead',
        steps: [
          { content: 'read spec', status: 'completed' },
          { content: 'write code', status: 'pending' },
        ],
      },
    ]);

    expect(normalizeAgentEvents({ type: 'plan', steps: [] })).toEqual([]);
    expect(normalizeAgentEvents({ type: 'plan' })).toEqual([]);
  });

  it('carries ACP-style permission options through normalization', () => {
    expect(
      normalizeAgentEvents({
        type: 'permission_request',
        request_id: 'r1',
        command: 'npm test',
        options: [
          { optionId: 'allow', name: 'Allow' },
          { optionId: 'deny', name: 'Deny' },
        ],
      }),
    ).toEqual([
      {
        type: 'permission_requested',
        request_id: 'r1',
        command: 'npm test',
        options: [
          { id: 'allow', label: 'Allow' },
          { id: 'deny', label: 'Deny' },
        ],
      },
    ]);
  });
});
