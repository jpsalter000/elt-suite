{% test unique_combination_of_columns(model, combination) %}
-- Rows whose combination of columns is not unique.
select {{ combination | join(', ') }}, count(*) as occurrences
from {{ model }}
group by {{ combination | join(', ') }}
having count(*) > 1
{% endtest %}
