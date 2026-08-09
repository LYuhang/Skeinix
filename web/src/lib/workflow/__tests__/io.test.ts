/**
 * Stream 6 — workflow JSON IO helpers (pure).
 */
import { describe, expect, it } from 'vitest';
import {
  serializeWorkflow,
  parseUploadedWorkflow,
  selectPortableWorkflow,
  downloadFilename,
  WorkflowParseError,
} from '@/lib/workflow/io';

const NODE = (type: string) => ({ node_id: 'x', node_type: type, children: [] });

describe('serializeWorkflow', () => {
  it('round-trips a draft as pretty JSON', () => {
    const draft = { node_1: NODE('StartNode'), __meta__: { workflow_name: 'A' } };
    const json = serializeWorkflow(draft);
    expect(JSON.parse(json)).toEqual(draft);
    expect(json).toContain('\n'); // pretty-printed
  });

  it('serializes null draft as {}', () => {
    expect(serializeWorkflow(null)).toBe('{}');
  });
});

describe('selectPortableWorkflow', () => {
  it('keeps node entries and __meta__, but drops other identity keys', () => {
    const out = selectPortableWorkflow({
      node_1: NODE('StartNode'),
      node_2: NODE('EndNode'),
      __meta__: { workflow_name: 'A' },
      __version__: 3,
      wf_id: 'abc',
    });
    expect(Object.keys(out).sort()).toEqual(['__meta__', 'node_1', 'node_2']);
    expect(out.__meta__).toEqual({ workflow_name: 'A' });
  });
});

describe('parseUploadedWorkflow', () => {
  it('preserves the complete __meta__ object across a download-upload round trip', () => {
    const original = {
      node_1: NODE('StartNode'),
      __meta__: {
        workflow_name: 'Portable workflow',
        active_v: 7,
        enabled: false,
        labels: ['reviewed', 'portable'],
        settings: {
          code_requirements: 'pandas==2.2.0\nopenpyxl==3.1.5',
          timeout_seconds: 0,
          network_policy: { mode: 'restricted', allowed_hosts: ['api.example.test'] },
        },
        future_extension: { nested: { value: 'must survive' } },
      },
    };

    const downloaded = serializeWorkflow(original);
    const { workflow: uploaded } = parseUploadedWorkflow(JSON.parse(downloaded));

    expect(uploaded).toEqual(original);
  });

  it('preserves meta and code requirements with the uploaded nodes', () => {
    const { workflow } = parseUploadedWorkflow({
      node_1: NODE('StartNode'),
      __meta__: {
        workflow_name: 'A',
        active_v: 5,
        settings: { code_requirements: 'pandas==2.2.0' },
      },
    });
    expect(Object.keys(workflow)).toEqual(['node_1', '__meta__']);
    expect(workflow.node_1).toMatchObject({ node_type: 'StartNode' });
    expect(workflow.__meta__).toMatchObject({
      settings: { code_requirements: 'pandas==2.2.0' },
    });
  });

  it('rejects a non-object', () => {
    expect(() => parseUploadedWorkflow([1, 2, 3])).toThrow(WorkflowParseError);
    expect(() => parseUploadedWorkflow('nope')).toThrow(WorkflowParseError);
  });

  it('rejects a dict with zero node entries', () => {
    expect(() => parseUploadedWorkflow({ __meta__: {} })).toThrow(
      WorkflowParseError,
    );
  });

  it('rejects a malformed node entry (no node_type)', () => {
    expect(() =>
      parseUploadedWorkflow({ node_1: { node_id: 'x' } }),
    ).toThrow(WorkflowParseError);
  });

  it('rejects a malformed __meta__ entry', () => {
    expect(() => parseUploadedWorkflow({
      node_1: NODE('StartNode'),
      __meta__: 'not-an-object',
    })).toThrow(WorkflowParseError);
  });
});

describe('downloadFilename', () => {
  it('slugifies name + appends version + .json', () => {
    expect(downloadFilename('My Flow!', 'v2.sv1')).toBe('My_Flow_v2.sv1.json');
  });

  it('falls back to workflow when name is empty', () => {
    expect(downloadFilename('', null)).toBe('workflow.json');
    expect(downloadFilename(null, null)).toBe('workflow.json');
  });
});
