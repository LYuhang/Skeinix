from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from vibecanvas_api.storage.models import Base


EXPECTED_TABLES = {
    "execution_plans",
    "execution_plan_revisions",
    "execution_plan_runs",
    "execution_node_runs",
    "execution_node_attempts",
    "execution_node_outputs",
    "execution_plan_controls",
    "execution_plan_events",
    "execution_plan_run_events",
    "execution_plan_control_deliveries",
}


def test_execution_plan_domain_is_registered_with_encrypted_payload_columns():
    assert EXPECTED_TABLES <= set(Base.metadata.tables)
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "tenant_id" in table.c
        assert "ROW LEVEL" not in ddl  # RLS is migration-owned, not ORM magic.
    assert {
        "private_ciphertext",
        "private_nonce",
        "private_key_id",
    } <= set(Base.metadata.tables["execution_plan_revisions"].c.keys())
    assert {
        "payload_ciphertext",
        "payload_nonce",
        "payload_key_id",
    } <= set(Base.metadata.tables["execution_node_outputs"].c.keys())
