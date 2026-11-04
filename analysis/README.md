# analysis (Phase 4 placeholder)

Notebooks and scripts for the competitor study's write-up. Nothing here yet.

Planned:

* Push and email cadence per app × cohort × market, read against
  `v_instance_daily_coverage` so that downtime never looks like "no sends".
* Trigger attribution: compare `send_class` and `seconds_since_event` with what the
  cohort did (`sessions`, `days_since_last_open`, `opens_last_7d`).
* Relabelling. `send_class` is a first-pass heuristic (see `ingest/ingest/classify.py`).
  Once real payloads exist, map `notification_channel_id` and campaign ids per app.
* UI atlas: component presence by sport, tier and capture point from `components`.

Use the same Python environment as the rest of the repo, adding pandas and jupyter as needed.
Connect with `DATABASE_URL` from `.env`.
