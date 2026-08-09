"""Browser chat lease projection, HITL records, and interactive artifacts.

Revision ID: 042
Revises: 041
Create Date: 2026-07-17
"""
from alembic import op


revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS browser_control_status TEXT NOT NULL DEFAULT 'inactive'")
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS browser_session_id TEXT")
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS browser_session_generation BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS browser_last_event_seq BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS browser_lost_at TIMESTAMPTZ")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_chats_browser_control_status'
            ) THEN
                ALTER TABLE chats ADD CONSTRAINT ck_chats_browser_control_status
                CHECK (browser_control_status IN ('inactive','attaching','attached','lost'));
            END IF;
        END $$;
    """)
    op.execute("DROP INDEX IF EXISTS uq_chats_active_browser_lease")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_chats_active_browser_lease
        ON chats (tenant_id, creator_user_id)
        WHERE deleted_at IS NULL
          AND browser_control_status IN ('attaching','attached','lost')
    """)

    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS input_message_id TEXT")
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS base_checkpoint_id TEXT")
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS result_checkpoint_id TEXT")
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS ck_agent_runs_status")
    op.execute("""
        ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_status CHECK (
            status IN (
                'running', 'waiting_approval',
                'cancel_requested', 'completed', 'cancelled', 'failed'
            )
        )
    """)
    op.execute("DROP INDEX IF EXISTS uq_agent_runs_one_active_chat")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_chat
        ON agent_runs (chat_id)
        WHERE status IN ('running', 'waiting_approval', 'cancel_requested')
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS hitl_requests (
            hitl_request_id        TEXT PRIMARY KEY,
            tenant_id              UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                                   DEFAULT current_setting('app.tenant_id', true)::uuid,
            chat_id                TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            run_id                 TEXT REFERENCES agent_runs(run_id) ON DELETE SET NULL,
            artifact_id            TEXT,
            hitl_type              TEXT NOT NULL,
            status                 TEXT NOT NULL DEFAULT 'pending',
            title                  TEXT NOT NULL DEFAULT '',
            prompt_text            TEXT NOT NULL DEFAULT '',
            ui_payload_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
            agent_payload_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
            decision_payload_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
            resume_payload_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_interacted          BOOLEAN NOT NULL DEFAULT FALSE,
            interaction_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            expires_at             TIMESTAMPTZ,
            resolved_at            TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_hitl_requests_type CHECK (
                hitl_type IN ('pre_tool_approval','post_tool_review','elicitation')
            ),
            CONSTRAINT ck_hitl_requests_status CHECK (
                status IN ('pending','approved','denied','submitted','cancelled','expired')
            )
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_hitl_requests_chat_status
        ON hitl_requests (tenant_id, chat_id, status, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_hitl_requests_run_status
        ON hitl_requests (tenant_id, run_id, status, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_hitl_requests_artifact
        ON hitl_requests (tenant_id, artifact_id)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS interactive_artifacts (
            artifact_id              TEXT PRIMARY KEY,
            tenant_id                UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                                     DEFAULT current_setting('app.tenant_id', true)::uuid,
            chat_id                  TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            run_id                   TEXT REFERENCES agent_runs(run_id) ON DELETE SET NULL,
            hitl_request_id          TEXT REFERENCES hitl_requests(hitl_request_id) ON DELETE SET NULL,
            component_type           TEXT NOT NULL,
            completion_mode          TEXT NOT NULL DEFAULT 'render_only',
            title                    TEXT NOT NULL DEFAULT '',
            definition_json          JSONB NOT NULL DEFAULT '{}'::jsonb,
            widget_state_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
            interaction_result_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_interacted            BOOLEAN NOT NULL DEFAULT FALSE,
            artifact_ref             TEXT,
            content_hash             TEXT,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_interactive_artifacts_completion_mode CHECK (
                completion_mode IN ('render_only','wait_for_submit')
            )
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_interactive_artifacts_chat_created
        ON interactive_artifacts (tenant_id, chat_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_interactive_artifacts_run_created
        ON interactive_artifacts (tenant_id, run_id, created_at DESC)
    """)

    for table in ("hitl_requests", "interactive_artifacts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO vibecanvas_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON interactive_artifacts")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON hitl_requests")
    op.execute("DROP TABLE IF EXISTS interactive_artifacts CASCADE")
    op.execute("DROP TABLE IF EXISTS hitl_requests CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_agent_runs_one_active_chat")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_chat
        ON agent_runs (chat_id)
        WHERE status IN ('running', 'waiting_approval', 'cancel_requested')
    """)
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS ck_agent_runs_status")
    op.execute("""
        ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_status CHECK (
            status IN (
                'running', 'waiting_approval', 'cancel_requested',
                'completed', 'cancelled', 'failed'
            )
        )
    """)
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS result_checkpoint_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS base_checkpoint_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS input_message_id")
    op.execute("DROP INDEX IF EXISTS uq_chats_active_browser_lease")
    op.execute("ALTER TABLE chats DROP CONSTRAINT IF EXISTS ck_chats_browser_control_status")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS browser_lost_at")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS browser_last_event_seq")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS browser_session_generation")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS browser_session_id")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS browser_control_status")
