import { describe, it, expect } from 'vitest';
import {
  ensureOutputFieldDescriptions,
  ensureHTTPRequestConfig,
  ensureTransformMappings,
  normalizeForSend,
} from '../normalize';

describe('ensureOutputFieldDescriptions', () => {
  it('backfills description="" on output fields missing the key', () => {
    const wf = {
      __meta__: { workflow_id: 'w' },
      node_1: {
        node_type: 'StartNode',
        output_fields: {
          text: { type: 'string' }, // missing description (the reported bug)
          named: { type: 'integer', description: 'n' },
        },
      },
    };
    const out = ensureOutputFieldDescriptions(wf) as typeof wf;
    expect(out.node_1.output_fields.text).toEqual({ type: 'string', description: '' });
    expect(out.node_1.output_fields.named).toEqual({ type: 'integer', description: 'n' });
    expect(out.__meta__).toBe(wf.__meta__); // __meta__ passthrough
  });

  it('reuses a fully-described node by reference (no needless clone)', () => {
    const node = {
      node_type: 'EndNode',
      output_fields: { v: { type: 'string', description: 'd' } },
    };
    const wf = { node_1: node };
    const out = ensureOutputFieldDescriptions(wf);
    expect(out.node_1).toBe(node);
  });

  it('passes through nodes without output_fields', () => {
    const wf = { node_1: { node_type: 'StartNode', node_config: {} } };
    const out = ensureOutputFieldDescriptions(wf);
    expect(out.node_1).toBe(wf.node_1);
  });

  it('does not mutate the input', () => {
    const wf = { node_1: { output_fields: { x: { type: 'string' } } } };
    ensureOutputFieldDescriptions(wf);
    expect('description' in wf.node_1.output_fields.x).toBe(false);
  });
});

describe('ensureHTTPRequestConfig', () => {
  it('backfills method="GET" and url="" on an HTTP node missing them (the reported bug)', () => {
    const wf = {
      __meta__: { workflow_id: 'w' },
      node_1: {
        node_type: 'HTTPRequestNode',
        node_config: {}, // fresh node — no method → engine config["method"] KeyError
      },
    };
    const out = ensureHTTPRequestConfig(wf) as typeof wf;
    expect(out.node_1.node_config).toEqual({ method: 'GET', url: '' });
    expect(out.__meta__).toBe(wf.__meta__); // __meta__ passthrough
  });

  it('backfills only the absent required key, preserving a set method/url', () => {
    const wf = {
      node_1: {
        node_type: 'HTTPRequestNode',
        node_config: { method: 'POST', headers: { 'X-A': '1' } },
      },
    };
    const out = ensureHTTPRequestConfig(wf) as typeof wf;
    expect(out.node_1.node_config).toEqual({
      method: 'POST',
      headers: { 'X-A': '1' },
      url: '',
    });
  });

  it('reuses a node that already has both required keys by reference (no clone)', () => {
    const node = {
      node_type: 'HTTPRequestNode',
      node_config: { method: 'GET', url: 'https://example.com' },
    };
    const wf = { node_1: node };
    const out = ensureHTTPRequestConfig(wf);
    expect(out.node_1).toBe(node);
  });

  it('leaves non-HTTP nodes untouched', () => {
    const wf = { node_1: { node_type: 'CodeNode', node_config: {} } };
    const out = ensureHTTPRequestConfig(wf);
    expect(out.node_1).toBe(wf.node_1);
  });

  it('does not mutate the input', () => {
    const wf = { node_1: { node_type: 'HTTPRequestNode', node_config: {} } };
    ensureHTTPRequestConfig(wf);
    expect('method' in wf.node_1.node_config).toBe(false);
  });
});

describe('ensureTransformMappings', () => {
  it('prunes mappings whose output_field is no longer declared (the orphan)', () => {
    const wf = {
      __meta__: { workflow_id: 'w' },
      node_1: {
        node_type: 'TransformNode',
        output_fields: { kept: { type: 'string', description: '' } },
        node_config: {
          mappings: [
            { input_field: 'a', output_field: 'kept', transform_list: [] },
            // orphan — `gone` was renamed/removed from output_fields.
            { input_field: 'a', output_field: 'gone', transform_list: [] },
          ],
        },
      },
    };
    const out = ensureTransformMappings(wf) as typeof wf;
    expect(out.node_1.node_config.mappings).toEqual([
      { input_field: 'a', output_field: 'kept', transform_list: [] },
    ]);
    expect(out.__meta__).toBe(wf.__meta__); // __meta__ passthrough
  });

  it('reuses a node whose mappings are all valid by reference (no clone)', () => {
    const node = {
      node_type: 'TransformNode',
      output_fields: { a: { type: 'string' }, b: { type: 'string' } },
      node_config: {
        mappings: [
          { input_field: 'x', output_field: 'a', transform_list: [] },
          { input_field: 'y', output_field: 'b', transform_list: [] },
        ],
      },
    };
    const wf = { node_1: node };
    const out = ensureTransformMappings(wf);
    expect(out.node_1).toBe(node);
  });

  it('migrates legacy compute expression to canonical expr', () => {
    const wf = {
      node_1: {
        node_type: 'TransformNode',
        output_fields: { label: { type: 'string' } },
        node_config: {
          mappings: [{
            input_field: 'x',
            output_field: 'label',
            transform_list: [
              { op: 'compute', expression: '"value=" + {value}' },
            ],
          }],
        },
      },
    };
    const out = ensureTransformMappings(wf) as typeof wf;
    expect(out.node_1.node_config.mappings[0].transform_list[0]).toEqual({
      op: 'compute',
      expr: '"value=" + {value}',
    });
    expect(wf.node_1.node_config.mappings[0].transform_list[0]).toHaveProperty(
      'expression',
    );
  });

  it('leaves non-Transform nodes untouched', () => {
    const wf = {
      node_1: {
        node_type: 'CodeNode',
        node_config: { mappings: [{ output_field: 'whatever' }] },
      },
    };
    const out = ensureTransformMappings(wf);
    expect(out.node_1).toBe(wf.node_1);
  });

  it('passes through a Transform node without mappings', () => {
    const wf = { node_1: { node_type: 'TransformNode', node_config: {} } };
    const out = ensureTransformMappings(wf);
    expect(out.node_1).toBe(wf.node_1);
  });

  it('does not mutate the input', () => {
    const wf = {
      node_1: {
        node_type: 'TransformNode',
        output_fields: {},
        node_config: {
          mappings: [{ input_field: 'a', output_field: 'gone', transform_list: [] }],
        },
      },
    };
    ensureTransformMappings(wf);
    expect(wf.node_1.node_config.mappings).toHaveLength(1);
  });
});

describe('normalizeForSend', () => {
  it('composes all passes — backfills HTTP method together with output descriptions', () => {
    const wf = {
      node_1: {
        node_type: 'HTTPRequestNode',
        node_config: {},
        output_fields: { response_body: { type: 'object' } },
      },
    };
    const out = normalizeForSend(wf) as typeof wf;
    expect(out.node_1.node_config).toEqual({ method: 'GET', url: '' });
    expect(out.node_1.output_fields.response_body).toEqual({
      type: 'object',
      description: '',
    });
  });
});
