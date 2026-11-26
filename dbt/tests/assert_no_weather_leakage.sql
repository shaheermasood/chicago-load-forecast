-- Fails if a weather feature comes from a model run that wasn't published at issue time.
select target_period_utc, weather_lead_days, weather_run_time_est_utc, issue_time_utc
from {{ ref('fct_hourly_features') }}
where weather_lead_days is not null
    and weather_run_time_est_utc
        > issue_time_utc - interval {{ var('weather_run_latency_hours') }} hour
