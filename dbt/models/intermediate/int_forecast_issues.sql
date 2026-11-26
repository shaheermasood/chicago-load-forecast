-- One row per target local day: when its forecast is issued (10:00 Central the day before),
-- the latest load hour that is usable at that moment, and load summaries up to that hour.
--
-- An hour's value (hour starting at h) is complete at h + 1h and assumed published
-- load_latency_hours later. So at issue time the newest usable hour starts at
--     issue_time_utc - (load_latency_hours + 1) hours.

with days as (
    select distinct cast(period_local as date) as target_date_local
    from {{ ref('int_load_hourly') }}
    union
    select distinct cast({{ to_local('period_utc') }} as date)
    from {{ ref('int_weather_zone_hourly') }}
    where weather_source = 'forecast'
),

issues as (
    select
        target_date_local,
        (target_date_local - interval 1 day) + interval {{ var('issue_hour_local') }} hour
            as issue_time_local
    from days
),

issues_utc as (
    select
        *,
        {{ from_local('issue_time_local') }} as issue_time_utc,
        {{ from_local('issue_time_local') }}
            - interval {{ var('load_latency_hours') + 1 }} hour as load_known_through_utc
    from issues
),

windowed as (
    select
        i.target_date_local,
        avg(l.load_mw) filter (
            where l.period_utc > i.load_known_through_utc - interval 24 hour
        ) as load_mean_24h_at_issue_mw,
        avg(l.load_mw) as load_mean_168h_at_issue_mw,
        max(l.load_mw) filter (
            where l.period_utc > i.load_known_through_utc - interval 24 hour
        ) as load_max_24h_at_issue_mw,
        arg_max(l.load_mw, l.period_utc) filter (where l.load_mw is not null)
            as load_last_known_mw,
        max(l.period_utc) filter (where l.load_mw is not null) as load_last_known_utc
    from issues_utc as i
    inner join {{ ref('int_load_hourly') }} as l
        on l.period_utc <= i.load_known_through_utc
        and l.period_utc > i.load_known_through_utc - interval 168 hour
    group by 1
)

select
    i.target_date_local,
    i.issue_time_local,
    i.issue_time_utc,
    i.load_known_through_utc,
    w.load_last_known_mw,
    w.load_last_known_utc,
    w.load_mean_24h_at_issue_mw,
    w.load_mean_168h_at_issue_mw,
    w.load_max_24h_at_issue_mw
from issues_utc as i
left join windowed as w using (target_date_local)
