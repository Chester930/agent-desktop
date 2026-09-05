export type AgentEvent =
  | {
      type: 'run_started';
      run_id?: string;
      agent?: string;
      engine?: string;
    }
  | {
      type: 'text_delta';
      text: string;
      agent?: string;
      step?: number;
    }
  | {
      type: 'tool_call_start';
      id: string;
      name: string;
      input: unknown;
      agent?: string;
      /** ACP `tool_call` status (`pending`/`in_progress`); absent means unknown/legacy source. */
      status?: 'pending' | 'in_progress';
    }
  | {
      type: 'tool_call_end';
      id: string;
      output: unknown;
      is_error: boolean;
      agent?: string;
      /** ACP `tool_call_update` status (`completed`/`failed`); absent means unknown/legacy source. */
      status?: 'completed' | 'failed';
    }
  | {
      type: 'permission_requested';
      request_id?: string;
      command?: string;
      agent?: string;
      /** ACP `RequestPermissionRequest.options` — the choices offered to the user. */
      options?: { id: string; label: string }[];
    }
  | {
      /** ACP `plan` session/update — the agent's declared sequence of steps. */
      type: 'plan';
      steps: { content: string; status: 'pending' | 'in_progress' | 'completed' }[];
      agent?: string;
    }
  | {
      type: 'member_started';
      agent: string;
    }
  | {
      type: 'member_finished';
      agent: string;
    }
  | {
      type: 'usage_updated';
      usage: Record<string, unknown>;
    }
  | {
      type: 'run_error';
      text: string;
      agent?: string;
    }
  | {
      type: 'run_finished';
      session_id?: string;
      usage?: Record<string, unknown>;
      cost_usd?: number;
    };

type JsonRecord = {
  [key: string]: any;
  type?: any;
  message?: any;
  text?: any;
  agent?: any;
  step?: any;
  id?: any;
  name?: any;
  input?: any;
  is_error?: any;
  tool_use_id?: any;
  content?: any;
  session_id?: any;
  usage?: any;
  run_id?: any;
  engine?: any;
  request_id?: any;
  command?: any;
  output?: any;
  total_cost_usd?: any;
  status?: any;
  options?: any;
  optionId?: any;
  label?: any;
  steps?: any;
};

export function normalizeAgentEvents(raw: unknown): AgentEvent[] {
  if (!isRecord(raw) || typeof raw.type !== 'string') return [];

  switch (raw.type) {
    case 'assistant':
      return normalizeAssistantMessage(raw);
    case 'user':
      return normalizeToolResults(raw);
    case 'text':
    case 'exec_text':
    case 'step_text':
      return typeof raw.text === 'string'
        ? [
            {
              type: 'text_delta',
              text: raw.text,
              agent: asString(raw.agent),
              step: asNumber(raw.step),
            },
          ]
        : [];
    case 'tool_use':
      return normalizeToolUse(raw);
    case 'agent_start':
    case 'exec_start':
      return typeof raw.agent === 'string' ? [{ type: 'member_started', agent: raw.agent }] : [];
    case 'agent_done':
    case 'exec_done':
      return typeof raw.agent === 'string' ? [{ type: 'member_finished', agent: raw.agent }] : [];
    case 'error':
      return typeof raw.text === 'string'
        ? [{ type: 'run_error', text: raw.text, agent: asString(raw.agent) }]
        : [];
    case 'permission_request':
      return [
        {
          type: 'permission_requested',
          request_id: asString(raw.request_id),
          command: asString(raw.command),
          agent: asString(raw.agent),
          options: asPermissionOptions(raw.options),
        },
      ];
    case 'result':
      return [
        {
          type: 'run_finished',
          session_id: asString(raw.session_id),
          usage: asRecord(raw.usage),
          cost_usd: asNumber(raw.total_cost_usd),
        },
      ];
    case 'run_started':
      return [
        {
          type: 'run_started',
          run_id: asString(raw.run_id),
          agent: asString(raw.agent),
          engine: asString(raw.engine),
        },
      ];
    case 'text_delta':
      return typeof raw.text === 'string'
        ? [
            {
              type: 'text_delta',
              text: raw.text,
              agent: asString(raw.agent),
              step: asNumber(raw.step),
            },
          ]
        : [];
    case 'tool_call_start':
      return normalizeToolUse(raw);
    case 'tool_call_end':
      return typeof raw.id === 'string'
        ? [
            {
              type: 'tool_call_end',
              id: raw.id,
              output: raw.output,
              is_error: raw.is_error === true,
              agent: asString(raw.agent),
            },
          ]
        : [];
    case 'permission_requested':
      return [
        {
          type: 'permission_requested',
          request_id: asString(raw.request_id),
          command: asString(raw.command),
          agent: asString(raw.agent),
          options: asPermissionOptions(raw.options),
        },
      ];
    // ACP-style session/update names, kept separate from the legacy
    // `tool_call_start`/`tool_call_end` branches above so existing engine
    // output cannot accidentally pick up a `status` it never sent.
    case 'tool_call': {
      if (typeof raw.id !== 'string') return [];
      return [
        {
          type: 'tool_call_start',
          id: raw.id,
          name: asString(raw.name) ?? '',
          input: raw.input ?? {},
          agent: asString(raw.agent),
          status: raw.status === 'in_progress' ? 'in_progress' : 'pending',
        },
      ];
    }
    case 'tool_call_update': {
      if (typeof raw.id !== 'string') return [];
      const failed = raw.status === 'failed';
      return [
        {
          type: 'tool_call_end',
          id: raw.id,
          output: raw.output,
          is_error: failed,
          agent: asString(raw.agent),
          status: failed ? 'failed' : 'completed',
        },
      ];
    }
    case 'plan': {
      const steps = asPlanSteps(raw.steps);
      return steps ? [{ type: 'plan', steps, agent: asString(raw.agent) }] : [];
    }
    case 'member_started':
    case 'member_finished':
      return typeof raw.agent === 'string' ? [{ type: raw.type, agent: raw.agent }] : [];
    case 'usage_updated':
      return asRecord(raw.usage) ? [{ type: 'usage_updated', usage: raw.usage }] : [];
    case 'run_error':
      return typeof raw.text === 'string'
        ? [{ type: 'run_error', text: raw.text, agent: asString(raw.agent) }]
        : [];
    case 'run_finished':
      return [
        {
          type: 'run_finished',
          session_id: asString(raw.session_id),
          usage: asRecord(raw.usage),
          cost_usd: asNumber(raw['cost_usd']),
        },
      ];
    default:
      return [];
  }
}

function normalizeAssistantMessage(raw: JsonRecord): AgentEvent[] {
  const content = raw.message?.content;
  if (!Array.isArray(content)) return [];

  return content.flatMap((block: unknown) => {
    if (!isRecord(block)) return [];
    if (block.type === 'text' && typeof block.text === 'string') {
      return [
        { type: 'text_delta', text: block.text, agent: asString(raw.agent) } satisfies AgentEvent,
      ];
    }
    if (block.type === 'tool_use') return normalizeToolUse(block, asString(raw.agent));
    return [];
  });
}

function normalizeToolResults(raw: JsonRecord): AgentEvent[] {
  const content = raw.message?.content;
  if (!Array.isArray(content)) return [];

  return content.flatMap((block: unknown) => {
    if (!isRecord(block) || block.type !== 'tool_result' || typeof block.tool_use_id !== 'string')
      return [];
    return [
      {
        type: 'tool_call_end',
        id: block.tool_use_id,
        output: block.content,
        is_error: block.is_error === true,
        agent: asString(raw.agent),
      } satisfies AgentEvent,
    ];
  });
}

function normalizeToolUse(raw: JsonRecord, agent?: string): AgentEvent[] {
  if (typeof raw.id !== 'string' || typeof raw.name !== 'string') return [];
  return [{ type: 'tool_call_start', id: raw.id, name: raw.name, input: raw.input ?? {}, agent }];
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? value : undefined;
}

function asPermissionOptions(value: unknown): { id: string; label: string }[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const options = value
    .map((item) => {
      if (!isRecord(item)) return undefined;
      const id = asString(item.id) ?? asString(item.optionId);
      const label = asString(item.label) ?? asString(item.name);
      return id && label ? { id, label } : undefined;
    })
    .filter((item): item is { id: string; label: string } => item !== undefined);
  return options.length > 0 ? options : undefined;
}

function asPlanSteps(
  value: unknown,
): { content: string; status: 'pending' | 'in_progress' | 'completed' }[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const steps = value
    .map((item) => {
      if (!isRecord(item)) return undefined;
      const content = asString(item.content) ?? asString(item.text);
      if (!content) return undefined;
      const status: 'pending' | 'in_progress' | 'completed' =
        item.status === 'in_progress' || item.status === 'completed' ? item.status : 'pending';
      return { content, status };
    })
    .filter(
      (item): item is { content: string; status: 'pending' | 'in_progress' | 'completed' } =>
        item !== undefined,
    );
  return steps.length > 0 ? steps : undefined;
}
