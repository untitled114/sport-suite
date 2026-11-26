-- Database initialization
-- One PostgreSQL 16 + TimescaleDB instance, one schema per domain

-- Extensions (database-wide)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Sessions default to Eastern time (takes effect for new connections)
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET timezone = %L', current_database(), 'America/New_York');
END $$;

DO $$
BEGIN
    RAISE NOTICE 'Sport-Suite database initialized - extensions enabled';
END $$;
