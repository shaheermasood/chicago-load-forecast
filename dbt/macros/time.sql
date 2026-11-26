{#- Wall-clock time in the zone (naive TIMESTAMP) for a TIMESTAMPTZ column.
    Fall-back produces two UTC hours with the same local value and spring-forward skips one,
    so join and dedupe on the UTC column. Local times are for features and display only. -#}
{% macro to_local(col) -%}
    timezone('{{ var("local_tz") }}', {{ col }})
{%- endmacro %}

{#- TIMESTAMPTZ for a naive wall-clock timestamp in the zone (inverse of to_local). -#}
{% macro from_local(col) -%}
    timezone('{{ var("local_tz") }}', {{ col }})
{%- endmacro %}
