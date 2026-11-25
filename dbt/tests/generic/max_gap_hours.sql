{#- Fails for each jump between consecutive hourly timestamps larger than `max_hours`,
    optionally per group (e.g. per weather point). -#}
{% test max_gap_hours(model, column_name, max_hours, group_by=none) %}
with ordered as (
    select
        {{ column_name }} as ts,
        lag({{ column_name }}) over (
            {% if group_by %}partition by {{ group_by | join(', ') }}{% endif %}
            order by {{ column_name }}
        ) as prev_ts
        {% if group_by %}, {{ group_by | join(', ') }}{% endif %}
    from {{ model }}
)
select *, date_diff('hour', prev_ts, ts) as gap_hours
from ordered
where date_diff('hour', prev_ts, ts) > {{ max_hours }}
{% endtest %}
