import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MessageItem } from '../MessageItem';

describe('background job activity message', () => {
  it('renders a compact accessible pill and opens its durable batch', () => {
    const onOpen = vi.fn();
    render(
      <MessageItem
        message={{
          id: 'notice-1',
          role: 'system',
          content: '2 个后台任务已有结果 · 1 完成 · 1 失败',
          tool_calls: [],
          activity: {
            type: 'background_jobs_delivered',
            delivery_batch_id: 'bg_batch_1',
            job_ids: ['job_1', 'job_2'],
            summary: { completed: 1, failed: 1, cancelled: 0 },
          },
        }}
        onOpenBackgroundJobs={onOpen}
      />,
    );

    const activity = screen.getByRole('button', { name: /2 个后台任务/ });
    expect(activity).toHaveAttribute('data-role', 'background-job-activity');
    expect(activity).toHaveAttribute('data-delivery-batch-id', 'bg_batch_1');
    fireEvent.click(activity);
    expect(onOpen).toHaveBeenCalledWith({
      jobId: undefined,
      deliveryBatchId: 'bg_batch_1',
    });
  });
});
