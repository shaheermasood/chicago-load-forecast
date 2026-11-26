-- Zone-average weather: the population-weighted mean of the weather points, per hour and
-- source. Forecast rows keep lead_days; reanalysis rows have lead_days = 0.
--
-- If a point is missing for an hour, the average is taken over the points present (weights
-- renormalised) and n_points says how many contributed.

with points as (
    select point, weight from {{ ref('weather_points') }}
),

unioned as (
    select 'forecast' as weather_source, period_utc, lead_days, point,
        temp_f, dew_point_f, apparent_temp_f, rh_pct, wind_mph, cloud_cover_pct
    from {{ ref('stg_open_meteo__weather_forecast') }}
    union all
    select 'reanalysis' as weather_source, period_utc, 0 as lead_days, point,
        temp_f, dew_point_f, apparent_temp_f, rh_pct, wind_mph, cloud_cover_pct
    from {{ ref('stg_open_meteo__weather_reanalysis') }}
)

select
    weather_source,
    period_utc,
    lead_days,
    {% for c in ['temp_f', 'dew_point_f', 'apparent_temp_f', 'rh_pct', 'wind_mph', 'cloud_cover_pct'] -%}
    sum({{ c }} * weight) / nullif(sum(case when {{ c }} is not null then weight end), 0) as {{ c }},
    {% endfor -%}
    count(*) as n_points,
    min(temp_f) as temp_min_point_f, -- spread across the zone (lake vs inland)
    max(temp_f) as temp_max_point_f
from unioned
inner join points using (point)
group by all
