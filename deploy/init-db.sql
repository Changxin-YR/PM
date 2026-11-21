\set ON_ERROR_STOP on
\getenv owner_pass PMO_DEPLOY_MIGRATE_PASSWORD
\getenv api_pass PMO_DEPLOY_API_PASSWORD
\getenv worker_pass PMO_DEPLOY_OUTBOX_PASSWORD
\getenv bootstrap_pass POSTGRES_PASSWORD
SELECT COUNT(DISTINCT secret) = 4 AND bool_and(secret ~ '^[0-9a-fA-F]{64}$') AS valid
FROM (VALUES (:'owner_pass'), (:'api_pass'), (:'worker_pass'), (:'bootstrap_pass')) AS keys(secret) \gset
\if :valid
  CREATE ROLE pmo_migrate LOGIN PASSWORD :'owner_pass';
  CREATE ROLE pmo_api LOGIN PASSWORD :'api_pass';
  CREATE ROLE pmo_outbox LOGIN PASSWORD :'worker_pass';
\else
  \echo 'Set four distinct URL-safe 64-digit hexadecimal passwords.'
  SELECT 1/0;
\endif
\unset owner_pass
\unset api_pass
\unset worker_pass
\unset bootstrap_pass
ALTER DATABASE pmo OWNER TO pmo_migrate;
ALTER SCHEMA public OWNER TO pmo_migrate;
REVOKE ALL ON DATABASE pmo FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
