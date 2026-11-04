-- Competitor push & UI study: baseline schema.
-- Apply to an empty database. Later changes go in db/migrations/NNN_*.sql.
--
-- Conventions
--   * Every timestamp is timestamptz (stored UTC). Market-local views come from instances.tz.
--   * Controlled vocabularies are CHECK constraints rather than enums (cheaper to change).
--   * `raw` jsonb columns keep the original payload so nothing is lost if parsing changes.

begin;

create table schema_migrations (
  version    text primary key,
  applied_at timestamptz not null default now()
);

-- Tracked apps. Ingest maps an Android package to an app id through this table.
create table apps (
  id      text primary key check (id in ('365scores', 'fotmob', 'livescore')),
  package text not null unique,
  name    text not null
);

create table cohorts (
  id             text primary key check (id ~ '^C[0-9]+$'),    -- 'C1'
  name           text not null,
  market         text not null check (market in ('UK', 'DE', 'IT', 'ES', 'JP')),
  follows        jsonb not null default '{}'::jsonb,           -- {teams:[], leagues:[], players:[]}
  notif_settings jsonb not null default '{}'::jsonb,           -- {"preset": "defaults"} or explicit toggles
  behaviour      text,
  hypothesis     text,
  apps           text[] not null default array['365scores','fotmob','livescore']
);

create table instances (
  id             text primary key,                             -- 'uk-c4-fotmob' or 'atlas-1-fotmob'
  kind           text not null check (kind in ('cohort', 'atlas')),
  cohort_id      text references cohorts(id),
  app            text not null references apps(id),
  package        text not null references apps(package),
  market         text not null check (market in ('UK', 'DE', 'IT', 'ES', 'JP')),
  google_account text,                                         -- filled after legal sign-off
  email          text unique,
  device_model   text,
  locale         text not null,                                -- 'en-GB'
  tz             text not null,                                -- 'Europe/London'
  vpn_exit       text,                                         -- docker network / gateway name
  wave           text not null default 'core',
  created_at     timestamptz not null default now(),
  snapshot_path  text,
  constraint instance_kind_cohort check ((kind = 'atlas') = (cohort_id is null))
);
create index instances_cohort_idx on instances (cohort_id);

create table sessions (
  id          bigserial primary key,
  instance_id text not null references instances(id),
  started_at  timestamptz not null,
  ended_at    timestamptz,
  script      text not null,                                   -- flow name, e.g. 'daily_open'
  screens     jsonb not null default '[]'::jsonb,              -- [{screen, sport, match_id, at}]
  constraint session_order check (ended_at is null or ended_at >= started_at)
);
create index sessions_instance_started_idx on sessions (instance_id, started_at desc);
-- Fast lookup of sessions still running (used by nightly restart to skip live visits).
create index sessions_open_idx on sessions (instance_id) where ended_at is null;

create table match_events (
  id              bigserial primary key,
  source          text not null default 'unknown',             -- adapter name: 'fake', 'sportradar'
  source_event_id text not null,                               -- provider id, makes polling idempotent
  match_id        text not null,
  competition     text,
  sport           text not null,                               -- football, basketball, american_football, tennis, cricket, ice_hockey
  home            text,
  away            text,
  event_type      text not null check (event_type in (
                    'scheduled', 'lineups', 'start', 'score', 'period_end', 'extra',
                    'discipline', 'stoppage', 'end', 'postponed')),
  sub_type        text,                                        -- goal, try, wicket, tie_break, rain_delay ...
  minute          int,
  occurred_at     timestamptz not null,
  payload         jsonb not null default '{}'::jsonb,
  unique (source, source_event_id)
);
create index match_events_occurred_idx on match_events (occurred_at);
create index match_events_match_idx on match_events (match_id, occurred_at);

create table events (
  id                      bigserial primary key,
  received_at             timestamptz not null default now(),
  local_time              timestamptz,                         -- device post time (logger) or email Date
  instance_id             text not null references instances(id),
  app                     text not null references apps(id),
  cohort_id               text references cohorts(id),          -- null for atlas instances
  market                  text not null,
  channel                 text not null check (channel in ('push', 'live_update', 'email', 'in_app')),
  package                 text,
  notification_key        text,                                -- StatusBarNotification key
  notification_channel_id text,
  title                   text,
  body                    text,
  big_text                text,
  campaign_id             text,
  crm_vendor              text,                                -- which extractor matched: braze, onesignal ...
  deep_link               text,
  extras                  jsonb not null default '{}'::jsonb,
  match_id                text,
  match_event_id          bigint references match_events(id),
  seconds_since_event     int,
  days_since_last_open    int,
  opens_last_7d           int,
  send_class              text check (send_class in (
                            'match_event', 'match_scheduled', 'content', 'lifecycle', 'commercial')),
  screenshot_path         text,
  action                  text,                                -- posted, removed, tapped ...
  downtime_adjacent       bool not null default false,
  dedupe_key              text not null,
  raw                     jsonb not null default '{}'::jsonb
);
create index events_app_cohort_received_idx on events (app, cohort_id, received_at);
create index events_instance_time_idx on events (instance_id, local_time);
create index events_dedupe_idx on events (instance_id, dedupe_key, local_time);
create index events_match_idx on events (match_id) where match_id is not null;

create table components (
  id              bigserial primary key,
  app             text not null references apps(id),
  sport           text not null,
  competition     text,
  tier            text check (tier in ('top', 'lower')),
  match_id        text,
  capture_point   text not null,                               -- 't-7d' ... 't+7d', 'rare_state'
  component       text not null,
  status          text not null check (status in ('present', 'partial', 'absent')),
  tab             text,
  position        int,
  above_fold      bool,
  updates_live    bool,
  screenshot_path text,
  labelled_by     text,
  checked         bool not null default false
);
create index components_app_sport_idx on components (app, sport, capture_point);

-- Windows when an instance could not have received anything. Always consult this
-- before reading a gap in `events` as "the app sent nothing".
create table instance_downtime (
  id          bigserial primary key,
  instance_id text not null references instances(id),
  started_at  timestamptz not null,
  ended_at    timestamptz,                                     -- null = still down
  reason      text not null,                                   -- nightly_restart, health_fail, wave_off, crash, maintenance
  detail      jsonb not null default '{}'::jsonb,
  constraint downtime_order check (ended_at is null or ended_at >= started_at)
);
create index instance_downtime_instance_idx on instance_downtime (instance_id, started_at);

-- Flag events that fall within `margin` of any downtime window. Called by ingest per
-- event and by ops after a downtime window is closed (re-flags events already stored).
create or replace function refresh_downtime_adjacent(margin interval default interval '10 minutes')
returns int language sql as $$
  with upd as (
    update events e set downtime_adjacent = true
    where not e.downtime_adjacent
      and exists (
        select 1 from instance_downtime d
        where d.instance_id = e.instance_id
          and coalesce(e.local_time, e.received_at) >= d.started_at - margin
          and coalesce(e.local_time, e.received_at) <= coalesce(d.ended_at, now()) + margin)
    returning 1)
  select count(*)::int from upd;
$$;

-- Minutes each instance was down per UTC day. Join this before treating a gap as "no sends".
create view v_instance_daily_coverage as
with days as (
  select i.id as instance_id, d::date as day
  from instances i
  cross join generate_series(
    (select coalesce(min(started_at), now()) from instance_downtime)::date,
    now()::date, interval '1 day') d
)
select days.instance_id, days.day,
       coalesce(sum(extract(epoch from
         least(coalesce(dt.ended_at, now()), days.day + interval '1 day')
         - greatest(dt.started_at, days.day::timestamptz)) / 60), 0)::int as minutes_down
from days
left join instance_downtime dt
  on dt.instance_id = days.instance_id
 and dt.started_at < days.day + interval '1 day'
 and coalesce(dt.ended_at, now()) > days.day
group by days.instance_id, days.day;

insert into apps (id, package, name) values
  ('365scores', 'com.scores365',           '365Scores'),
  ('fotmob',    'com.mobilefootie.wc2010', 'FotMob'),
  ('livescore', 'com.livescore',           'LiveScore');

insert into schema_migrations (version) values ('000_baseline');

commit;
