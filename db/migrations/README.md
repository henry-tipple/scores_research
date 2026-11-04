# Migrations

`db/schema.sql` is the baseline (`000_baseline`). Put every later change in a file
named `NNN_short_name.sql` here (e.g. `001_add_events_locale.sql`). `make migrate`
(also run by `make seed`) applies the baseline if the database is empty, then any
migration whose version is not yet in `schema_migrations`, each in its own
transaction. Never edit a migration once it has been applied anywhere shared.
