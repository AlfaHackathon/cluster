#!/bin/sh
# Create missing service databases on fresh AND existing volumes. Never drop data.
set -eu

for db in "${USER_SERVICE_DB:-user-service}" "${COMPANY_SERVICE_DB:-company-service}" \
          "${CONTEXT_SERVICE_DB:-context-service}" "${INSIGHT_SERVICE_DB:-insight-service}"; do
  # psql quotes names/literals; configurable database names are never shell/SQL code.
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -v database_name="$db" <<'SQL'
SELECT format('CREATE DATABASE %I', :'database_name')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database_name')
\gexec
SQL
done
