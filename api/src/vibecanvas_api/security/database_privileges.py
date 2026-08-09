"""Live database-role gates for production processes.

Static DSN checks cannot prove PostgreSQL role attributes.  These probes run
against the connected identity and fail startup when a long-lived process can
change schema or silently bypass tenant RLS.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class DatabaseRoleFacts:
    role_name: str
    is_superuser: bool
    bypasses_rls: bool
    can_create_database: bool
    can_create_role: bool
    can_replicate: bool
    can_create_in_database: bool
    can_create_in_schema: bool
    owns_schema_objects: bool
    can_assume_dangerous_role: bool


class DatabasePrivilegeError(RuntimeError):
    def __init__(self, *, mode: str, codes: tuple[str, ...]):
        self.mode = mode
        self.codes = codes
        super().__init__(
            f"database {mode} role rejected: {', '.join(codes)}"
        )


_ROLE_FACTS_SQL = text(
    """
    SELECT current_user AS role_name,
           r.rolsuper AS is_superuser,
           r.rolbypassrls AS bypasses_rls,
           r.rolcreatedb AS can_create_database,
           r.rolcreaterole AS can_create_role,
           r.rolreplication AS can_replicate,
           has_database_privilege(
             current_user, current_database(), 'CREATE'
           ) AS can_create_in_database,
           has_schema_privilege(
             current_user, current_schema(), 'CREATE'
           ) AS can_create_in_schema,
           EXISTS (
             SELECT 1
               FROM pg_class c
               JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = current_schema()
                AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
                AND c.relowner = r.oid
           ) AS owns_schema_objects,
           EXISTS (
             SELECT 1
               FROM pg_roles inherited
              WHERE inherited.rolname <> current_user
                AND pg_has_role(current_user, inherited.oid, 'MEMBER')
                AND (
                  inherited.rolsuper OR inherited.rolbypassrls
                  OR inherited.rolcreatedb OR inherited.rolcreaterole
                  OR inherited.rolreplication
                  OR inherited.rolname IN (
                    'pg_read_server_files', 'pg_write_server_files',
                    'pg_execute_server_program', 'pg_signal_backend',
                    'pg_monitor'
                  )
                  OR has_database_privilege(
                    inherited.rolname, current_database(), 'CREATE'
                  )
                  OR has_schema_privilege(
                    inherited.rolname, current_schema(), 'CREATE'
                  )
                  OR EXISTS (
                    SELECT 1
                      FROM pg_class owned
                      JOIN pg_namespace owned_ns
                        ON owned_ns.oid=owned.relnamespace
                     WHERE owned_ns.nspname=current_schema()
                       AND owned.relowner=inherited.oid
                  )
                )
           ) AS can_assume_dangerous_role
      FROM pg_roles r
     WHERE r.rolname = current_user
    """
)


async def inspect_database_role(engine: AsyncEngine) -> DatabaseRoleFacts:
    async with engine.connect() as connection:
        row = (await connection.execute(_ROLE_FACTS_SQL)).mappings().one()
    return DatabaseRoleFacts(**row)


async def verify_database_role(engine: AsyncEngine, *, mode: str) -> None:
    """Verify a long-lived ``runtime`` or ``maintenance`` connection.

    Maintenance may bypass RLS for explicit cross-tenant control jobs, but it
    is still forbidden from owning objects or performing DDL.  Migration
    credentials are intentionally not accepted here: they belong only in the
    one-shot deployment workload.
    """
    if mode not in {"runtime", "maintenance"}:
        raise ValueError("mode must be runtime or maintenance")
    facts = await inspect_database_role(engine)
    codes: list[str] = []
    checks = (
        (facts.is_superuser, "superuser"),
        (facts.can_create_database, "createdb"),
        (facts.can_create_role, "createrole"),
        (facts.can_replicate, "replication"),
        (facts.can_create_in_database, "database_create"),
        (facts.can_create_in_schema, "schema_create"),
        (facts.owns_schema_objects, "object_owner"),
        (facts.can_assume_dangerous_role, "dangerous_role_membership"),
    )
    codes.extend(code for failed, code in checks if failed)
    if mode == "runtime" and facts.bypasses_rls:
        codes.append("bypassrls")
    if mode == "maintenance" and not facts.bypasses_rls:
        codes.append("bypassrls_required")
    if codes:
        raise DatabasePrivilegeError(mode=mode, codes=tuple(codes))
