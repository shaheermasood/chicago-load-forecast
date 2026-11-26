-- One row per target hour, with every feature as it was known when that hour's forecast was
-- issued (10:00 Central the day before). This is the only table models train or predict from.
--
-- The leakage rules are applied here and checked by tests/assert_no_*_leakage.sql:
--   * a load lag is NULL unless its hour was usable at issue time (int_forecast_issues)
--   * weather comes from the most recent forecast run that was already published at issue time
--     (lead_days 1 or 2; ADR 0006)
--   * ERA5 "actual" weather is NOT here; it lives in fct_hourly_weather_oracle

{% set load_lags = [48, 72, 168, 336] %}
{% set weather_cols = ['temp_f', 'dew_point_f', 'apparent_temp_f', 'rh_pct', 'wind_mph', 'cloud_cover_pct'] %}

with load as (
    select * from {{ ref('int_load_hourly') }}
),

forecast_weather as (
    select * from {{ ref('int_weather_zone_hourly') }}
    where weather_source = 'forecast'
),

spine as (
    -- Every hour with load or forecast weather. Hours past the last load (tomorrow, when
    -- forecasting live) are included with load_mw NULL.
    select range as target_period_utc
    from range(
        (select min(period_utc) from load),
        greatest(
            (select max(period_utc) from load),
            coalesce((select max(period_utc) from forecast_weather), (select max(period_utc) from load))
        ) + interval 1 hour,
        interval 1 hour
    )
),

targets as (
    select
        s.target_period_utc,
        {{ to_local('s.target_period_utc') }} as target_period_local,
        cast({{ to_local('s.target_period_utc') }} as date) as target_date_local
    from spine as s
),

with_issue as (
    select
        t.*,
        i.issue_time_utc,
        i.load_known_through_utc,
        date_diff('hour', i.issue_time_utc, t.target_period_utc) as horizon_hours,
        -- Most recent weather run already published at issue time: a lead-N value comes from
        -- the run started about N days before the target hour.
        case
            when t.target_period_utc - interval 1 day
                <= i.issue_time_utc - interval {{ var('weather_run_latency_hours') }} hour then 1
            when t.target_period_utc - interval 2 day
                <= i.issue_time_utc - interval {{ var('weather_run_latency_hours') }} hour then 2
        end as weather_lead_days,
        i.load_last_known_mw,
        i.load_mean_24h_at_issue_mw,
        i.load_mean_168h_at_issue_mw,
        i.load_max_24h_at_issue_mw
    from targets as t
    inner join {{ ref('int_forecast_issues') }} as i using (target_date_local)
),

holidays as (
    -- A date can carry two names (e.g. a holiday and its observed day); keep the strongest tier.
    select holiday_date, arg_min(holiday_tier,
        case holiday_tier when 'major' then 1 when 'bridge' then 2 else 3 end) as holiday_tier
    from {{ ref('holidays') }}
    group by 1
)

select
    -- keys and timing
    f.target_period_utc,
    f.target_period_local,
    f.target_date_local,
    f.issue_time_utc,
    f.horizon_hours,
    date_diff('hour', {{ from_local('f.target_date_local') }},
        {{ from_local('f.target_date_local + interval 1 day') }}) as hours_in_local_day,

    -- target (NULL for future hours and long gaps) and its quality flags
    l.load_mw,
    coalesce(l.is_imputed, false) as is_load_imputed,
    coalesce(l.is_anomalous, false) as is_load_anomalous,
    coalesce(l.is_reported_missing, true) as is_load_reported_missing,

    -- load history known at issue time
    {% for k in load_lags -%}
    case when f.target_period_utc - interval {{ k }} hour <= f.load_known_through_utc
        then lag_{{ k }}.load_mw end as load_lag_{{ k }}h_mw,
    {% endfor -%}
    f.load_known_through_utc,
    f.load_last_known_mw,
    f.load_mean_24h_at_issue_mw,
    f.load_mean_168h_at_issue_mw,
    f.load_max_24h_at_issue_mw,

    -- calendar (local time)
    hour(f.target_period_local) as hour_local,
    isodow(f.target_period_local) as dow_local, -- 1 = Monday
    month(f.target_period_local) as month_local,
    dayofyear(f.target_period_local) as doy_local,
    sin(2 * pi() * hour(f.target_period_local) / 24) as hour_sin,
    cos(2 * pi() * hour(f.target_period_local) / 24) as hour_cos,
    sin(2 * pi() * (isodow(f.target_period_local) - 1) / 7) as dow_sin,
    cos(2 * pi() * (isodow(f.target_period_local) - 1) / 7) as dow_cos,
    sin(2 * pi() * (dayofyear(f.target_period_local) - 1) / 365.25) as doy_sin,
    cos(2 * pi() * (dayofyear(f.target_period_local) - 1) / 365.25) as doy_cos,
    isodow(f.target_period_local) >= 6 as is_weekend,
    h.holiday_tier,
    coalesce(h.holiday_tier = 'major', false) as is_major_holiday,
    coalesce(h.holiday_tier = 'bridge', false)
        or (
            -- a weekday squeezed between a major holiday and the weekend
            isodow(f.target_period_local) in (1, 5)
            and coalesce(
                (isodow(f.target_period_local) = 5 and h_prev.holiday_tier = 'major')
                or (isodow(f.target_period_local) = 1 and h_next.holiday_tier = 'major'),
                false
            )
        ) as is_bridge_day,
    date_diff('hour', cast(f.target_period_utc as timestamp), f.target_period_local) = -5
        as is_dst,
    year(f.target_date_local) = 2020 as is_2020, -- COVID-era load shift (test with and without)

    -- weather forecast for the target hour, as issued
    f.weather_lead_days,
    {% for c in weather_cols -%}
    w.{{ c }},
    {% endfor -%}
    greatest(w.temp_f - {{ var('degree_base_f') }}, 0) as cooling_degree_f,
    greatest({{ var('degree_base_f') }} - w.temp_f, 0) as heating_degree_f,
    w.temp_max_point_f - w.temp_min_point_f as temp_spread_f,
    w.n_points as weather_n_points,
    f.target_period_utc - to_days(f.weather_lead_days) as weather_run_time_est_utc

from with_issue as f
left join load as l on l.period_utc = f.target_period_utc
{% for k in load_lags -%}
left join load as lag_{{ k }} on lag_{{ k }}.period_utc = f.target_period_utc - interval {{ k }} hour
{% endfor -%}
left join forecast_weather as w
    on w.period_utc = f.target_period_utc and w.lead_days = f.weather_lead_days
left join holidays as h on h.holiday_date = f.target_date_local
left join holidays as h_prev on h_prev.holiday_date = f.target_date_local - 1
left join holidays as h_next on h_next.holiday_date = f.target_date_local + 1
