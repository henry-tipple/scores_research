-- Runs once when the Postgres volume is first created (docker-entrypoint-initdb.d).
-- The main study database is created by POSTGRES_DB. These are the extras.
create database metabase;
create database study_test;
