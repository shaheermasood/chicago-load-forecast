-- Hourly weather forecasts per point and lead time, as issued (Previous Runs API).
-- Grain: period_utc x point x lead_days.

with source as (
    select * from {{ source('open_meteo', 'previous_runs') }}
),

typed as (
    select
        cast(period_utc as timestamptz) as period_utc,
        cast(point as varchar) as point,
        cast(lead_days as integer) as lead_days,
        {{ weather_value_columns() }},
        cast(fetched_at_utc as timestamptz) as fetched_at_utc
    from source
)

select
    period_utc,
    {{ to_local('period_utc') }} as period_local,
    point,
    lead_days,
    {{ weather_value_columns(cast=false) }},
    fetched_at_utc
from typed
qualify row_number() over (
    partition by period_utc, point, lead_days order by fetched_at_utc desc
) = 1
