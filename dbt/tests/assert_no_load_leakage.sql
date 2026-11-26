-- Fails if any load feature uses an hour that wasn't usable at issue time.
{% set load_lags = [48, 72, 168, 336] %}
select target_period_utc, 'load_known_through_utc after issue' as problem
from {{ ref('fct_hourly_features') }}
where load_known_through_utc >= issue_time_utc
{% for k in load_lags %}
union all
select target_period_utc, 'load_lag_{{ k }}h_mw is after the cutoff'
from {{ ref('fct_hourly_features') }}
where load_lag_{{ k }}h_mw is not null
    and target_period_utc - interval {{ k }} hour > load_known_through_utc
{% endfor %}
union all
select target_period_utc, 'issue is not before the target hour'
from {{ ref('fct_hourly_features') }}
where horizon_hours <= 0
