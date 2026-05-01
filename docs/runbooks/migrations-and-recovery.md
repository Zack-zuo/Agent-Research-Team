# Migrations and Recovery Runbook

Stage 7 supports automatic migration from schema `0.1.0` to `0.2.0`. Any other schema version fails fast with `unsupported_schema_version`.

## Migration behavior

Migration updates `project.yaml`, `state/project.json`, `state/adapters/health.json`, and `state/hooks/config.json`. Each mutated existing file is copied under `state/migrations/backups/<migration-id>/`. A completed record is written under `state/migrations/records/<migration-id>.json` with source version, target version, status, backup root, mutated paths, warnings, and timestamps.

Migration emits `schema.migration_started` and `schema.migrated` events. If migration fails, preserve the project root before editing state manually.

## Recovery steps

1. Save the exact command error and copy the project root if possible.
2. Inspect the named canonical entity under `state/`.
3. Restore missing canonical files from regular backups or migration backups.
4. Avoid repairing only inboxes, outboxes, report aliases, hook logs, or artifact indexes when a canonical entity is missing.
5. Rerun `open_project`; then run `request_status`.

Preflight may recreate support directories, hook config, daily logs, artifact indexes, and latest aliases. These repairs appear as warnings in command results.
