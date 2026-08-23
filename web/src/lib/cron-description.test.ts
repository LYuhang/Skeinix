import { describe, expect, it } from 'vitest';

import { describeCronExpression } from './cron-description';

describe('describeCronExpression', () => {
  it('explains a weekday schedule in natural language', () => {
    expect(describeCronExpression('15 8 * * 1-5', 'zh')).toEqual({
      text: '每周一至周五 08:15',
      valid: true,
    });
    expect(describeCronExpression('15 8 * * 1-5', 'en')).toEqual({
      text: '08:15 AM, every Monday through Friday',
      valid: true,
    });
  });

  it('explains common hourly, daily, weekly and monthly schedules', () => {
    expect(describeCronExpression('45 * * * *', 'zh').text).toBe('每小时的第 45 分钟');
    expect(describeCronExpression('0 9 * * *', 'zh').text).toBe('每天 09:00');
    expect(describeCronExpression('30 18 * * 1', 'zh').text).toBe('每周一 18:30');
    expect(describeCronExpression('0 7 1 * *', 'zh').text).toBe('每月 1 日 07:00');
  });

  it('does not invent a meaning for unsupported syntax', () => {
    expect(describeCronExpression('not a cron expression', 'zh')).toEqual({
      text: '自定义计划',
      valid: false,
    });
  });
});
