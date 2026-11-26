-- Hourly ERA5 reanalysis per point: what the weather actually was.
-- Grain: period_utc x point. Used for the oracle ablation, never as a live feature.

with source as (
    select * from {{ source('open_meteo', 'era5') }}
),

typed as (
    select
        cast(period_utc as timestamptz) as period_utc,
        cast(point as varchar) as point,
        {{ weather_value_columns() }},
        cast(fetched_at_utc as timestamptz) as fetched_at_utc
    from source
)

select
    period_utc,
    {{ to_local('period_utc') }} as period_local,
    point,
    {{ weather_value_columns(cast=false) }},
    fetched_at_utc
from typed
qualify row_number() over (partition by period_utc, point order by fetched_at_utc desc) = 1
