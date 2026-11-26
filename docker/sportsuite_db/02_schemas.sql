-- Create all schemas and grant access
-- One schema per domain, so each can be granted, backed up and reasoned about on its own.

CREATE SCHEMA IF NOT EXISTS players;
CREATE SCHEMA IF NOT EXISTS games;
CREATE SCHEMA IF NOT EXISTS teams;
CREATE SCHEMA IF NOT EXISTS intelligence;
CREATE SCHEMA IF NOT EXISTS features;     -- feature store

-- Grant schema access to the application user
DO $$
DECLARE
    s TEXT;
BEGIN
    FOREACH s IN ARRAY ARRAY['players','games','teams','intelligence','features']
    LOOP
        EXECUTE format('GRANT ALL ON SCHEMA %I TO %I', s, current_user);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON TABLES TO %I', s, current_user);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON SEQUENCES TO %I', s, current_user);
    END LOOP;
END $$;
