# CLAUDE.md

Guidance for Claude Code sessions in this repo.

## Project

This is a DAZN Sports Data competitor study of 365Scores, FotMob and LiveScore on Android.
It measures push and email frequency, what triggers those sends, and UI state across sports.

* **Fleet.** ReDroid containers run 32 cohort instances (12 cohorts × 3 apps, minus FotMob for
  C11 and C12, and C6 Premium on FotMob only, the one app with a paid tier) plus 6 atlas
  instances, 38 in total.
* **Data path.** Logger APK and Mailpit feed the ingest service, which writes the Postgres
  `events` table. A match-context feed is joined to each send, and Metabase is the dashboard.

## Hard constraints (until legal sign-off)

- **Competitor apps.** Never install, download, decompile or run 365Scores, FotMob or
  LiveScore, and never fetch their APKs. Selectors stay `TODO(selector)`.
- **Google accounts.** Never create Google accounts or touch Play Store sign-in.
- **Secrets.** Never commit secrets, credentials, VPN configs, API keys or real email domains.
  Use `.env` (gitignored) and add the placeholder to `.env.example`. The email domain in dev is `example.test`.
- **Data.** Use fake data only. Everything must run on a dev machine with Docker.
- **Sportradar.** The adapter stays a stub: no live calls, no keys.

## Layout

| Directory | What's in it |
|---|---|
| `infra/` | Compose stack, Dockerfile (one image for ingest and tools), systemd templates, host notes |
| `db/` | `schema.sql` is the baseline; changes go in `migrations/NNN_name.sql`, never in edits to applied files. Also `migrate.py` and `seed.py`. |
| `fleet/` | `cohorts.yaml` (12 cohorts, the single source of truth) and `fleet.yaml` (apps, markets, resources, waves, devices). `fleet/fleet/generate.py` produces seed SQL, ReDroid commands and the manifest. |
| `ingest/` | FastAPI app (`ingest/ingest/main.py`). Also `campaign.py` (pluggable CRM extractors), `classify.py` (provisional `send_class` and match join), `store.py` (windowed dedupe) and `fake_events.py`. |
| `feeds/` | `FeedAdapter` protocol, fake replay adapter (`feeds/samples/*.json`), Sportradar stub and poller |
| `ops/` | `health`, `restart`, `waves`, `snapshot` and `flow_runner`. All shell-outs go through an injectable runner (`DryRunner` in tests and `--dry-run`). |
| `flows/` | Maestro templates and `selectors/<app>.yaml`. The runner passes every variable as `-e`. |
| `logger-apk/` | Kotlin NotificationListenerService. Never built so far (no SDK in the scaffolding session). |
| `analysis/` | Phase 4 placeholder |

Python imports use top-level package names via `PYTHONPATH`
(`ingest`, `feeds`, `ops`, `fleet`, `db`). There is no install step; see `pyproject.toml`
(`[tool.pytest.ini_options].pythonpath`) and `infra/Dockerfile`.

## Run and test

```bash
make up && make seed     # stack + 12 cohorts + 38 instances
make fake-events         # fake day through the real ingest paths (incl. Mailpit webhook)
make test                # pytest in the tools container; DB tests use study_test (reset each run)
make fleet               # generator output in fleet/out/
```

* **Host-side tests.** Run `uv venv -p 3.12 .venv && uv pip install -p .venv -r requirements.txt`, then
  `TEST_DATABASE_URL=postgresql://study:study@localhost:5432/study_test .venv/bin/pytest`.
  DB tests skip without `TEST_DATABASE_URL`, so `make test` is the authoritative run.
* **Rebuild after editing.** The ingest container copies code in at build time, so run `make up`
  again (it passes `--build`) after changing `ingest/`. The tools container mounts the repo live.
* **Version pins.** `requirements.txt` mirrors `pyproject.toml` dependencies; keep them in sync.

## Conventions

- **Timestamps.** Use `timestamptz` everywhere. `events.local_time` is the device post time (or the email
  `Date`); `received_at` is when ingest saw it.
- **Vocabularies.** Use CHECK constraints, not enums. When you add a value, add a migration.
- **Downtime.** Anything that takes an instance offline must write `instance_downtime` through
  `ops.common.Downtime`. Silence is only meaningful against `v_instance_daily_coverage`.
- **Push dedupe.** Same instance + campaign_id within 60 s. Live-update cards key on content
  (one row per change). Email dedupes on Message-ID.
- **Instance IDs.** Cohort instances are `<market>-<cohort>-<app>` (lowercase), which is also the email local part.
  Atlas instances are `atlas-<n>-<app>`.
- **Device profiles.** These are per cohort, never per app, so the three apps in a cohort run on the same phone.
