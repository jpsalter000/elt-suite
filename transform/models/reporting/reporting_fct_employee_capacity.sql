-- Published to Power BI as reporting.fct_employee_capacity.
select
    employee_id,
    date_day,
    standard_hours::numeric(8, 2) as standard_hours,
    exempt_hours::numeric(8, 2) as exempt_hours,
    expected_hours::numeric(8, 2) as expected_hours
from {{ ref('fct_employee_capacity') }}
