#!/usr/bin/env bash
set -euo pipefail

# The official PostgreSQL image executes this only for a fresh data volume.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=literature_db=literature_research <<'SQL'
SELECT format('CREATE DATABASE %I', :'literature_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'literature_db')
\gexec
SQL
