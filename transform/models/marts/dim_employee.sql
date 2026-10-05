with employees as (
    select * from {{ ref('stg_netsuite__employees') }}
),

departments as (
    select * from {{ ref('stg_netsuite__departments') }}
),

groups as (
    select * from {{ ref('department_groups') }}
),

calendar_end as (
    select max(date_day) as last_day from {{ ref('int_calendar') }}
)

select
    employees.employee_id,
    employees.employee_name,
    employees.first_name,
    employees.last_name,
    employees.email,
    employees.department_id,
    departments.department_name,
    coalesce(groups.group_name, departments.department_name) as group_name,
    employees.title,
    employees.hire_date,
    employees.release_date,
    case
        when employees.release_date < calendar_end.last_day then 'Released'
        else 'Current'
    end as employment_status,
    employees.standard_hours_per_day,
    employees.cost_per_hour,
    employees.burdened_cost_per_hour,
    employees.is_inactive
from employees
cross join calendar_end
left join departments on employees.department_id = departments.department_id
left join groups on departments.department_name = groups.department_name
