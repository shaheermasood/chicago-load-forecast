-- ERA5 reanalysis ("what the weather actually was") per target hour.
-- For the ablation in Phase 6 only: it measures how much weather-forecast error costs.
-- Never a feature for a real forecast, which is why it's kept out of fct_hourly_features.

select
    period_utc as target_period_utc,
    temp_f as oracle_temp_f,
    dew_point_f as oracle_dew_point_f,
    apparent_temp_f as oracle_apparent_temp_f,
    rh_pct as oracle_rh_pct,
    wind_mph as oracle_wind_mph,
    cloud_cover_pct as oracle_cloud_cover_pct,
    greatest(temp_f - {{ var('degree_base_f') }}, 0) as oracle_cooling_degree_f,
    greatest({{ var('degree_base_f') }} - temp_f, 0) as oracle_heating_degree_f,
    n_points as oracle_n_points
from {{ ref('int_weather_zone_hourly') }}
where weather_source = 'reanalysis'
