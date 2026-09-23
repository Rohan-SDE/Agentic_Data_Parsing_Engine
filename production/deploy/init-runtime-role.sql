-- Executed by the official PostgreSQL entrypoint only on a fresh database volume.
-- pg_read_file keeps the password out of shell arguments and environment variables.
SELECT format('CREATE ROLE adpe_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
              rtrim(pg_read_file('/run/secrets/runtime_password'), E'\n')) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE adpe TO adpe_runtime;
GRANT USAGE ON SCHEMA public TO adpe_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO adpe_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO adpe_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE adpe IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO adpe_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE adpe IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO adpe_runtime;
