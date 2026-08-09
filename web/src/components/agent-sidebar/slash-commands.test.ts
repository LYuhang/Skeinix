import { describe, expect, it } from 'vitest';
import { slashCommandsFromCatalog } from './slash-commands';

describe('slashCommandsFromCatalog', () => {
  it('uses the backend catalog order without a duplicated frontend registry', () => {
    expect(slashCommandsFromCatalog([
      'task',
      'deployment',
      'knowledge',
      'build',
      'task',
    ])).toEqual([
      { trigger: '/task', descKey: 'composer.cmd.task' },
      { trigger: '/deployment', descKey: 'composer.cmd.deployment' },
      { trigger: '/knowledge', descKey: 'composer.cmd.knowledge' },
      { trigger: '/build', descKey: 'composer.cmd.build' },
    ]);
  });
});
