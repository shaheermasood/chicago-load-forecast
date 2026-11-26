{#- Weather value columns shared by the forecast and reanalysis staging models. Names match
    the column names in configs/model.yaml (weather.variables), with units in the name. -#}
{% macro weather_value_columns(cast=true) -%}
    {%- set cols = ['temp_f', 'dew_point_f', 'apparent_temp_f', 'rh_pct', 'wind_mph', 'cloud_cover_pct'] -%}
    {%- for c in cols -%}
        {% if cast %}cast({{ c }} as double) as {{ c }}{% else %}{{ c }}{% endif %}
        {%- if not loop.last %},
    {% endif -%}
    {%- endfor -%}
{%- endmacro %}
