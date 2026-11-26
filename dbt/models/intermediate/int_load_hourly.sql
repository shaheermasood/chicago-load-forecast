-- Continuous hourly ComEd load: one row for every UTC hour from the first to the last
-- reported hour, whether EIA reported it or not.
--
-- Nothing is dropped silently. Each hour keeps what EIA reported and gets flags:
--   is_reported_missing  EIA has no row or no value for the hour
--   is_anomalous         reported value is out of range, or a one-hour spike away from both neighbours
--   is_imputed           load_mw was linearly interpolated across a short gap (<= max_interp_gap_hours)
-- load_mw is NULL only for longer gaps, which models must handle (or skip).

with reported as (
    select period_utc, load_mw as reported_load_mw
    from {{ ref('stg_eia__hourly_demand') }}
),

spine as (
    select range as period_utc
    from range(
        (select min(period_utc) from reported),
        (select max(period_utc) from reported) + interval 1 hour,
        interval 1 hour
    )
),

joined as (
    select
        s.period_utc,
        r.reported_load_mw,
        lag(r.reported_load_mw) over w as prev_mw,
        lead(r.reported_load_mw) over w as next_mw
    from spine as s
    left join reported as r using (period_utc)
    window w as (order by s.period_utc)
),

flagged as (
    select
        *,
        reported_load_mw is null as is_reported_missing,
        coalesce(
            reported_load_mw not between {{ var('load_min_mw') }} and {{ var('load_max_mw') }}
            -- One-hour spike: jumps away from BOTH neighbours, in the same direction, by more
            -- than load_spike_pct. (Comparing to the neighbours' mean would also flag the normal
            -- hour next to a real spike.)
            or (
                sign(reported_load_mw - prev_mw) = sign(reported_load_mw - next_mw)
                and abs(reported_load_mw - prev_mw) > {{ var('load_spike_pct') }} * prev_mw
                and abs(reported_load_mw - next_mw) > {{ var('load_spike_pct') }} * next_mw
            ),
            false
        ) as is_anomalous
    from joined
),

cleaned as (
    select
        *,
        case when not is_anomalous then reported_load_mw end as clean_mw
    from flagged
),

neighbours as (
    -- Nearest clean value on each side, and when it was.
    select
        *,
        last_value(clean_mw ignore nulls) over before as before_mw,
        last_value(case when clean_mw is not null then period_utc end ignore nulls)
            over before as before_utc,
        first_value(clean_mw ignore nulls) over after as after_mw,
        first_value(case when clean_mw is not null then period_utc end ignore nulls)
            over after as after_utc
    from cleaned
    window
        before as (order by period_utc rows between unbounded preceding and 1 preceding),
        after as (order by period_utc rows between 1 following and unbounded following)
),

imputed as (
    select
        *,
        case
            when clean_mw is null
                and before_utc is not null and after_utc is not null
                and date_diff('hour', before_utc, after_utc) - 1 <= {{ var('max_interp_gap_hours') }}
            then before_mw + (after_mw - before_mw)
                * date_diff('hour', before_utc, period_utc)
                / date_diff('hour', before_utc, after_utc)
        end as interpolated_mw
    from neighbours
)

select
    period_utc,
    {{ to_local('period_utc') }} as period_local,
    coalesce(clean_mw, interpolated_mw) as load_mw,
    reported_load_mw,
    is_reported_missing,
    is_anomalous,
    clean_mw is null and interpolated_mw is not null as is_imputed
from imputed
