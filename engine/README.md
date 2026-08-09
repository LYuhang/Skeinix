# Skeinix workflow engine

The engine is Skeinix's framework-independent Python workflow runtime. Its
Python import name is `vibecanvas_engine`. The package does not depend on
FastAPI or the Web application.

## Install from source

```bash
cd ..
uv python install 3.11.15
uv venv --python 3.11.15 --seed .venv
uv pip install --python .venv/bin/python --requirement requirements-dev.txt
uv pip install --python .venv/bin/python --no-deps --editable ./engine
source .venv/bin/activate
```

Python 3.11.15 is the supported development interpreter. Do not use Conda or
install the project into the host Python environment.

## Python API

```python
from vibecanvas_engine import Workflow

workflow_data = {"__meta__": {"name": "example"}}
result = Workflow.check(workflow_data)
```

The public API includes workflow validation and execution, built-in node
classes, the node registry, provider-neutral LLM adapters, and isolated code
execution utilities.

## Model providers

The engine supports OpenAI-compatible endpoints and adapters for supported
providers. Applications should inject credentials at runtime and must not store
API keys in workflow documents or source code. Custom providers can implement
`BaseLLM` and register with `llm_registry`.

## Development

```bash
python -m pytest engine/tests
python -m build
```

Changes to engine dependencies or imports must preserve the framework boundary;
the API and Web packages may depend on the engine, but the engine must not
depend on them.

## License

Apache-2.0. See [`LICENSE`](../LICENSE).
