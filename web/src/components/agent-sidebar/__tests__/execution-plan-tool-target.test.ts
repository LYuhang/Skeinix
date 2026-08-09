import { describe, expect, it } from 'vitest';

import { executionPlanFromToolCall } from '@/components/agent-sidebar/tool-call-utils';
import type { MergedToolCall } from '@/components/agent-sidebar/types';

describe('executionPlanFromToolCall', () => {
  it('reads the trusted product handle emitted by create_execution_plan', () => {
    const call: MergedToolCall = {
      id: 'tool-1',
      name: 'create_execution_plan',
      arguments: '{"plan_path":"/data/plans/a.plan.json"}',
      status: 'done',
      artifact: {
        artifact: {
          handles: {
            execution_plan: {
              plan_id: 'plan_1',
              plan_run_id: 'planrun_1',
              revision: 2,
            },
          },
        },
      },
    };

    expect(executionPlanFromToolCall(call)).toEqual({
      planId: 'plan_1',
      runId: 'planrun_1',
      revision: 2,
    });
  });

  it('does not infer a plan target from another tool', () => {
    expect(executionPlanFromToolCall({
      id: 'tool-2', name: 'custom_tool', arguments: '', status: 'done',
    })).toBeNull();
  });
});
