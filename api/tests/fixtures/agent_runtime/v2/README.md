# Agent Runtime protocol v2 fixtures

These files freeze the SDK-neutral host/sandbox behavior accepted before a new
Runtime may be registered.

- `open.json` and `turn-base.json` define the shared session and Turn envelope.
- `scenarios.json` is the executable scenario manifest.
- Each JSONL file is one ordered event stream. Sequence numbers and the single
  terminal event are part of the contract.

The placeholders `$runtime_type`, `$runtime_root`, and `$model` are hydrated by
`test_runtime_v2_fixtures.py`. Every registered adapter must accept the same
envelopes and preserve these event, control, cancellation, projection, and
sanitized-failure semantics. Provider-native diagnostics may be added only
inside bounded, sanitized payload fields; they must not replace the canonical
event types or expose credentials.

When the protocol intentionally changes, update the protocol version, fixtures,
projection expectations, and all adapter conformance results together. Do not
rewrite v2 fixtures to make an incompatible adapter appear compliant.
