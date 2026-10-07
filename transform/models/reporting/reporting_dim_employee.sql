-- Published to Power BI as reporting.dim_employee.
select
    employee_id,
    employee_name::text as employee_name,
    department_name::text as department_name,
    group_name::text as group_name,
    title::text as title,
    hire_date,
    release_date,
    employment_status::text as employment_status,
    standard_hours_per_day::numeric(4, 2) as standard_hours_per_day
from {{ ref('dim_employee') }}
