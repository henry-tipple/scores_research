-- Starter views for Metabase. Market-local dates come from instances.tz.

-- Sends per instance per market-local day, by channel and class.
create or replace view v_sends_daily as
select e.app, e.cohort_id, e.market, e.instance_id,
       (coalesce(e.local_time, e.received_at) at time zone i.tz)::date as local_day,
       e.channel, e.send_class,
       count(*)                                         as sends,
       count(*) filter (where e.campaign_id is not null) as crm_sends,
       bool_or(e.downtime_adjacent)                     as any_downtime_adjacent
from events e
join instances i on i.id = e.instance_id
where coalesce(e.action, 'posted') in ('posted', 'received', 'active_on_connect')
group by 1, 2, 3, 4, 5, 6, 7;

-- Latency from match event to push, for event-driven sends.
create or replace view v_match_send_latency as
select e.app, e.cohort_id, e.market, e.channel, me.sport, me.competition,
       me.event_type, me.sub_type, e.match_id, e.seconds_since_event,
       e.local_time, e.title
from events e
join match_events me on me.id = e.match_event_id
where e.send_class = 'match_event';

-- Hour-of-day profile (market-local) of every send.
create or replace view v_send_hour_profile as
select e.app, e.cohort_id, e.market, e.channel, e.send_class,
       extract(hour from coalesce(e.local_time, e.received_at) at time zone i.tz)::int as local_hour,
       count(*) as sends
from events e
join instances i on i.id = e.instance_id
where coalesce(e.action, 'posted') in ('posted', 'received', 'active_on_connect')
group by 1, 2, 3, 4, 5, 6;
