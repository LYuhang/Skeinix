"""Remove the superseded Agent Phase planning ledger.

Revision ID: 119
Revises: 118
Create Date: 2026-08-10
"""

from alembic import op


revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS phase_events CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_plans CASCADE")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_plans (
            plan_id text PRIMARY KEY,
            run_id text NOT NULL,
            chat_id text NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            wf_id text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT 'planning',
            private_ciphertext text NOT NULL,
            private_nonce text NOT NULL,
            private_key_id uuid NOT NULL
                REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT,
            creator_user_id uuid NOT NULL REFERENCES users(user_id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id)
                DEFAULT current_setting('app.tenant_id', true)::uuid
        )
        """
    )
    op.execute("ALTER TABLE agent_plans ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_plans FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON agent_plans FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        """
        CREATE TABLE phase_events (
            id bigserial PRIMARY KEY,
            run_id text NOT NULL,
            phase_id text NOT NULL,
            ts timestamptz NOT NULL DEFAULT now(),
            event_type text NOT NULL,
            payload_ciphertext text NOT NULL,
            payload_nonce text NOT NULL,
            payload_key_id uuid NOT NULL
                REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
            CONSTRAINT ck_phase_events_event_type CHECK (
                event_type IN ('running','output','done','error','waiting_human')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_phase_events_run_id ON phase_events (run_id, id)"
    )
    op.execute("ALTER TABLE phase_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE phase_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON phase_events FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
