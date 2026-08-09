-- Three connection classes: short-lived migration, tenant runtime, and
-- cross-tenant maintenance.  Long-lived roles never own schema objects or
-- receive CREATE; the maintenance role may bypass RLS but is not a superuser.
--
-- The postgres image runs this script once on first volume init
-- (/docker-entrypoint-initdb.d/*.sql). On subsequent starts with the
-- existing volume the script is skipped — the role persists.
--
-- Passwords are supplied by the postgres container environment.  The final
-- ALTER statements use psql variables and SQL quoting, so fresh-server setup
-- never requires editing this tracked file and never interpolates a password
-- as executable SQL.

DO $$
BEGIN
    CREATE ROLE vibecanvas_app LOGIN;
EXCEPTION WHEN duplicate_object THEN
    NULL;   -- role already exists; idempotent
END
$$;

DO $$
BEGIN
    CREATE ROLE vibecanvas_migrator LOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE ROLE vibecanvas_maintenance LOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END
$$;

ALTER ROLE vibecanvas_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE vibecanvas_migrator NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
ALTER ROLE vibecanvas_maintenance
  NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;

GRANT CONNECT ON DATABASE vibecanvas
  TO vibecanvas_app, vibecanvas_migrator, vibecanvas_maintenance;
GRANT CREATE ON DATABASE vibecanvas TO vibecanvas_migrator;
REVOKE CREATE ON DATABASE vibecanvas
  FROM vibecanvas_app, vibecanvas_maintenance;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE, USAGE ON SCHEMA public TO vibecanvas_migrator;
GRANT USAGE ON SCHEMA public TO vibecanvas_app, vibecanvas_maintenance;

ALTER DEFAULT PRIVILEGES FOR ROLE vibecanvas_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
  TO vibecanvas_app, vibecanvas_maintenance;
ALTER DEFAULT PRIVILEGES FOR ROLE vibecanvas_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES
  TO vibecanvas_app, vibecanvas_maintenance;

\getenv app_password VIBECANVAS_APP_PASSWORD
\getenv migrator_password VIBECANVAS_MIGRATOR_PASSWORD
\getenv maintenance_password VIBECANVAS_MAINTENANCE_PASSWORD

SELECT format('ALTER ROLE vibecanvas_app PASSWORD %L', :'app_password') \gexec
SELECT format('ALTER ROLE vibecanvas_migrator PASSWORD %L', :'migrator_password') \gexec
SELECT format('ALTER ROLE vibecanvas_maintenance PASSWORD %L', :'maintenance_password') \gexec
