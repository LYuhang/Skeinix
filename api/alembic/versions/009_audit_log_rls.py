"""audit_log — action CHECK + tenant_id auto-fill + FORCE RLS (split
SELECT/INSERT policies) + append-only trigger + grants.

Revision ID: 009
Revises: 008
Create Date: 2026-05-30

The ``audit_log`` table itself is created by migration 001's
``Base.metadata.create_all(bind)``, which mirrors the current models
(``AuditLog`` registers via ``storage/models.py``). This migration only adds
what ``create_all`` cannot express (same RLS-only style as 003-008):

  - the action CHECK (the taxonomy's single SQL home; ``audit.actions``
    .AUDIT_ACTIONS is the Python mirror — the two lists MUST stay identical)
  - the tenant_id auto-fill default from ``app.tenant_id`` (resource write path)
  - row-level security (ENABLE + FORCE) + split SELECT/INSERT policies
  - the append-only BEFORE UPDATE OR DELETE trigger

Append-only is enforced by THREE independent layers (verified empirically —
the design spec's premise that "REVOKE from the owner is ineffective" turned
out FALSE in this PG/role setup, so all three actually bite):
  1. ``REVOKE UPDATE, DELETE`` from ``vibecanvas_app`` — EFFECTIVE here
     (``has_table_privilege(...,'UPDATE')`` → false after the revoke; owner
     UPDATE/DELETE raise InsufficientPrivilege). Do NOT remove it thinking it's
     a no-op — it is a live guard.
  2. FORCE RLS with no UPDATE/DELETE policy → those commands match 0 rows.
  3. The BEFORE UPDATE OR DELETE TRIGGER — ownership- AND privilege-independent
     (RAISEs even for a superuser who bypasses both 1 and 2; only DDL DISABLE
     TRIGGER bypasses it). This is the load-bearing guarantee proven in G1.
Keep all three (defense-in-depth); the trigger alone suffices, the other two
harden it.
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

# Mirror of audit.actions.AUDIT_ACTIONS (14 actions). Keep in sync.
_ACTIONS = (
    "'auth.login_success','auth.login_failure','auth.logout','auth.register',"
    "'auth.password_reset_request','auth.password_reset_complete',"
    "'deployment.key_rotate','mcp_server.credential_change',"
    "'deployment.create','deployment.delete','mcp_server.create',"
    "'mcp_server.delete','workflow.delete','kb.delete'"
)


def upgrade() -> None:
    # action CHECK (taxonomy lives here; AUDIT_ACTIONS is the Python mirror)
    op.execute(
        f"ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action "
        f"CHECK (action IN ({_ACTIONS}))"
    )

    # tenant_id auto-fill for the resource (tenant) write path — when the row
    # is inserted without an explicit tenant_id, fill from the GUC. The auth
    # write path lists tenant_id explicitly (incl. NULL) to bypass this.
    op.execute(
        "ALTER TABLE audit_log ALTER COLUMN tenant_id "
        "SET DEFAULT current_setting('app.tenant_id', true)::uuid"
    )

    # RLS: FORCE (vibecanvas_app owns the table) + split policies.
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_select ON audit_log FOR SELECT "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY audit_insert ON audit_log FOR INSERT "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    # No UPDATE/DELETE policy → denied by default (on top of the trigger).

    # Append-only trigger.
    op.execute(
        "CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "    RAISE EXCEPTION 'audit_log is append-only (no UPDATE/DELETE)';\n"
        "END;\n"
        "$$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_append_only "
        "BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()"
    )

    # Append-only layer 1 (defense-in-depth): REVOKE is EFFECTIVE on the owner
    # in this PG/role setup (contrary to the spec's premise) — owner UPDATE/
    # DELETE then raise InsufficientPrivilege. The trigger (above) is the
    # ownership-independent guarantee; this hardens it. The uuid PK uses
    # gen_random_uuid() (no sequence), so no sequence GRANT is needed.
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM vibecanvas_app")
    op.execute("GRANT INSERT, SELECT ON audit_log TO vibecanvas_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.execute("DROP POLICY IF EXISTS audit_insert ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_select ON audit_log")
    op.execute("ALTER TABLE audit_log NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log ALTER COLUMN tenant_id DROP DEFAULT")
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
