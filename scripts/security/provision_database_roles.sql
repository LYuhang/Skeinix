\set ON_ERROR_STOP on

-- Run as a PostgreSQL security administrator after creating the three LOGIN
-- identities through the platform's secret/IAM workflow.  This file never
-- accepts passwords on argv and never creates a fallback credential.
DO $$
BEGIN
  IF EXISTS (
    SELECT required.name
      FROM (VALUES ('vibecanvas_app'), ('vibecanvas_migrator'),
                   ('vibecanvas_maintenance')) AS required(name)
      LEFT JOIN pg_roles r ON r.rolname=required.name
     WHERE r.oid IS NULL
  ) THEN
    RAISE EXCEPTION 'create all three database login roles before provisioning';
  END IF;
END
$$;

ALTER ROLE vibecanvas_app
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE vibecanvas_migrator
  NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
ALTER ROLE vibecanvas_maintenance
  NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;

GRANT CONNECT ON DATABASE :"DBNAME"
  TO vibecanvas_app, vibecanvas_migrator, vibecanvas_maintenance;
GRANT CREATE ON DATABASE :"DBNAME" TO vibecanvas_migrator;
REVOKE CREATE ON DATABASE :"DBNAME"
  FROM vibecanvas_app, vibecanvas_maintenance;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE, USAGE ON SCHEMA public TO vibecanvas_migrator;
GRANT USAGE ON SCHEMA public TO vibecanvas_app, vibecanvas_maintenance;

-- Existing installations historically made vibecanvas_app the owner. Move
-- ownership before revoking DDL; this is metadata-only and preserves data.
REASSIGN OWNED BY vibecanvas_app TO vibecanvas_migrator;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
  TO vibecanvas_app, vibecanvas_maintenance;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
  TO vibecanvas_app, vibecanvas_maintenance;
ALTER DEFAULT PRIVILEGES FOR ROLE vibecanvas_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
  TO vibecanvas_app, vibecanvas_maintenance;
ALTER DEFAULT PRIVILEGES FOR ROLE vibecanvas_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES
  TO vibecanvas_app, vibecanvas_maintenance;

-- The append-only trigger is the primary audit guard. Remove unnecessary ACLs
-- as defense in depth when the table is already present.
SELECT 'REVOKE UPDATE, DELETE ON audit_log FROM '
       'vibecanvas_app, vibecanvas_maintenance'
WHERE to_regclass('public.audit_log') IS NOT NULL \gexec
