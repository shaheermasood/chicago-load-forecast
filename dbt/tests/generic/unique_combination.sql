{#- Fails for each key combination that appears more than once. -#}
{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n_rows
from {{ model }}
group by all
having count(*) > 1
{% endtest %}
