"""SP-3b-2b: add 'waiting_human' to phase_events.event_type CHECK.

Revision ID: 016
Revises: 015

Phases can now pause for human input — add the ``waiting_human`` event type
to the ``ck_phase_events_event_type`` CHECK on ``phase_events``. Mirrors the
SQLAlchemy ``CheckConstraint`` in ``models_phase_events.py``.
"""
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE phase_events DROP CONSTRAINT IF EXISTS ck_phase_events_event_type")
    op.execute(
        "ALTER TABLE phase_events ADD CONSTRAINT ck_phase_events_event_type "
        "CHECK (event_type IN ('running','output','done','error','waiting_human'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE phase_events DROP CONSTRAINT IF EXISTS ck_phase_events_event_type")
    op.execute(
        "ALTER TABLE phase_events ADD CONSTRAINT ck_phase_events_event_type "
        "CHECK (event_type IN ('running','output','done','error'))"
    )
