# scores_research: competitor push and UI study

DAZN Sports Data study of three Android scores apps: **365Scores**, **FotMob** and
**LiveScore**. It answers three questions:

1. How often does each app send push notifications and email?
2. Which user behaviours trigger those sends?
3. How does each app look and behave in every state, especially match pages before,
   during and after the game, for every sport?

**Method.** A fleet of ReDroid Android containers runs scripted user cohorts. There are
12 cohorts × 3 apps, minus the apps a cohort can't run on, gives **32 cohort instances**:
FotMob is football-only, so the multi-sport cohorts C11 and C12 skip it, and only FotMob has a
paid tier, so C6 Premium runs on FotMob alone. Two "atlas" instances per app capture UI, which
makes **6 atlas instances** and **38 in total**.

Data flow:
* A notification logger on each instance posts every notification to ingest.
* Each cohort has its own catch-all email address, delivered to Mailpit, whose webhook also posts to ingest.
* Everything lands in one Postgres `events` table.
* Ingest joins each send to a match-context feed, which tells event-driven sends apart from CRM sends.
* Metabase is the dashboard.

> **Status: scaffolding only.** Legal sign-off is pending. No competitor app has been
> installed, downloaded, decompiled or run, and no Google account exists. Everything runs on
> fake data. See [CLAUDE.md](CLAUDE.md) for the rules.

## Layout

```
infra/        docker-compose stack, Dockerfile, host setup notes, systemd timer templates
db/           schema.sql (baseline), migrations/, migrate.py, seed.py
ingest/       FastAPI ingest: logger JSON + Mailpit webhook -> events; fake-events generator
logger-apk/   Kotlin NotificationListenerService app (one APK per instance)
fleet/        cohorts.yaml, fleet.yaml, generator for instances + ReDroid docker run commands
flows/        Maestro YAML flows (cohort behaviours + atlas capture), per-app selector files
feeds/        match-context feed: adapter interface, fake replay adapter, Sportradar stub, poller
ops/          health check, nightly restart, wave scheduler, snapshots, Maestro flow runner
analysis/     Phase 4 notebooks (placeholder)
```

## Quick start (dev machine: Docker + make)

```bash
make up            # Postgres, Mailpit, ingest, Metabase (copies .env.example -> .env)
make seed          # schema + 12 cohorts + 38 instances
make fake-events   # a realistic fake day: pushes, live updates, emails (through Mailpit)
make test          # pytest suite in the tools container, against the study_test DB
make fleet         # fleet summary + fleet/out/{fleet.sh,redroid.txt,manifest.json,instances.sql}
make psql          # poke at the data
make down          # stop (make reset also deletes the volumes)
```

| URL | Service |
|---|---|
| http://localhost:8000/docs | ingest API |
| http://localhost:8025 | Mailpit inbox (SMTP on port 1025) |
| http://localhost:3000 | Metabase. Add the `study` database with host `postgres`, user/password `study`. |

## How the pieces fit

| Piece | Where |
|---|---|
| Push notification | `logger-apk` → `POST /v1/notification` |
| Email | Mailpit → webhook → `POST /v1/email`, with `POST /v1/email/sync` as a backfill |
| Behaviour | `ops.flow_runner` → Maestro → `POST /v1/sessions` |
| Match context | `feeds.poller` → `match_events` |
| Gaps | ops health, restart and waves → `instance_downtime` |

**What ingest does with each push or email:**

* Maps the package to an app and looks up cohort and market from `instances`.
* Sets `live_update` for ongoing cards.
* Pulls `campaign_id` using pluggable CRM extractors (Braze `cid`, OneSignal `custom.i`, and others).
* Dedupes: same instance + campaign within 60 s; live cards get one row per change.
* Joins the nearest matching `match_events` row and sets a provisional `send_class`.
* Snapshots `days_since_last_open` and `opens_last_7d` from `sessions`.
* Flags `downtime_adjacent` sends.

**Coverage.** Always read silence against `instance_downtime` (view
`v_instance_daily_coverage`). The nightly restart, health failures, wave switching and
snapshots all log windows there.

**Fleet sizing.** Set per-instance CPU and RAM in `fleet/fleet.yaml`. Waves let atlas
instances take turns. `make fleet` prints peak RAM against the host budget.

## What needs the real server or apps

* **Selectors.** Every Maestro selector is a `TODO(selector)` placeholder (`flows/selectors/*.yaml`).
* **Logger APK.** It builds and its unit tests pass, but it hasn't run on a device yet. See `logger-apk/README.md`.
* **Push campaign extractors.** Only Braze `cid` and OneSignal `i` come from the brief; the
  others are guesses (`ingest/ingest/campaign.py`).
* **VPN.** Per-market routing inside ReDroid has to be validated (`fleet/vpn/README.md`).
* **Sportradar.** The adapter is a stub and needs a contract and key.
* **Host and image.** The host, the GApps image and the email domain are placeholders in `.env.example`.
