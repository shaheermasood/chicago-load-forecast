-- One row per UTC hour of ComEd demand: types fixed, local time added, duplicates removed.
-- Missing or non-numeric loads are kept as NULL and flagged. Nothing is dropped here.

with source as (
    select * from {{ source('eia', 'hourly_demand') }}
),

typed as (
    select
        cast(period_utc as timestamptz) as period_utc,
        cast(parent as varchar) as parent_ba_code,
        cast(subba as varchar) as subregion_code,
        cast(load_mw as double) as load_mw,
        cast(fetched_at_utc as timestamptz) as fetched_at_utc
    from source
),

deduped as (
    -- Partitions are unique by construction; this guards against overlapping files anyway,
    -- keeping the most recently fetched value.
    select *
    from typed
    qualify row_number() over (partition by period_utc order by fetched_at_utc desc) = 1
)

select
    period_utc,
    {{ to_local('period_utc') }} as period_local,
    parent_ba_code,
    subregion_code,
    load_mw,
    load_mw is null as is_load_missing,
    fetched_at_utc
from deduped
