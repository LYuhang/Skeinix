import { describe, expect, it } from 'vitest';
import { parseAgentCommand } from './parseAgentCommand';

describe('parseAgentCommand', () => {
  it('parses a browser command and strips its prefix', () => {
    expect(parseAgentCommand('/browser open example.com')).toEqual({
      mode: 'browser',
      content: 'open example.com',
    });
  });

  it('keeps ordinary text in Chat mode', () => {
    expect(parseAgentCommand('hello')).toEqual({ mode: 'chat', content: 'hello' });
  });
});
