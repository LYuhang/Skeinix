from vibecanvas_engine.nodes.start import StartNode


def _start_node(input_fields: dict, output_fields: dict | None = None) -> dict:
    return {
        "node_id": "node_1",
        "node_name": "__start__",
        "node_type": "StartNode",
        "node_description": "Entry point",
        "input_fields": input_fields,
        "output_fields": output_fields or {
            name: {"type": info["type"], "description": name}
            for name, info in input_fields.items()
        },
        "node_config": {},
        "children": [],
        "__attributes__": {"x": 0, "y": 0},
    }


def test_start_node_requires_schema_for_array_input_field():
    node = _start_node({
        "items": {"type": "array", "value": [], "reference": ""},
    })

    result = StartNode.check(node)

    assert result["status"] == "error"
    assert "must include a non-empty detailed schema" in result["error_message"]


def test_start_node_requires_schema_type_to_match_complex_input_field_type():
    node = _start_node({
        "info": {
            "type": "object",
            "value": {},
            "reference": "",
            "schema": {"type": "array", "items": {"type": "string"}},
        },
    })

    result = StartNode.check(node)

    assert result["status"] == "error"
    assert "schema.type must match" in result["error_message"]


def test_start_node_accepts_array_schema_with_expanded_object_items():
    node = _start_node({
        "items": {
            "type": "array",
            "value": [],
            "reference": "",
            "schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        },
    })

    result = StartNode.check(node)

    assert result["status"] == "success"


def test_start_node_accepts_object_schema_with_expanded_array_property():
    node = _start_node({
        "info": {
            "type": "object",
            "value": {},
            "reference": "",
            "schema": {
                "type": "object",
                "properties": {
                    "images": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    })

    result = StartNode.check(node)

    assert result["status"] == "success"


def test_start_node_rejects_array_schema_with_unexpanded_nested_array_items():
    node = _start_node({
        "groups": {
            "type": "array",
            "value": [],
            "reference": "",
            "schema": {
                "type": "array",
                "items": {"type": "array"},
            },
        },
    })

    result = StartNode.check(node)

    assert result["status"] == "error"
    assert "schema.items.items is required for array schemas" in result["error_message"]


def test_start_node_rejects_object_schema_with_unexpanded_nested_object_property():
    node = _start_node({
        "info": {
            "type": "object",
            "value": {},
            "reference": "",
            "schema": {
                "type": "object",
                "properties": {
                    "profile": {"type": "object"},
                },
            },
        },
    })

    result = StartNode.check(node)

    assert result["status"] == "error"
    assert "schema.properties.profile.properties is required for object schemas" in result["error_message"]


def test_start_node_accepts_detailed_schema_for_complex_input_fields():
    node = _start_node({
        "items": {
            "type": "array",
            "value": [],
            "reference": "",
            "schema": {"type": "array", "items": {"type": "string"}},
        },
        "info": {
            "type": "object",
            "value": {},
            "reference": "",
            "schema": {
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    })

    result = StartNode.check(node)

    assert result["status"] == "success"
