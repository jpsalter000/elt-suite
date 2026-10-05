-- The Main Report: utilization per employee per calendar week (weeks start Sunday).
-- Time logged in a week counts even on days with no expected hours (weekends),
-- as in the workbook. Percentages are null when their denominator is zero.

with capacity as (
    select
        employee_id,
        date_day - extract(dow from date_day)::integer as week_start,
        sum(standard_hours) as standard_hours,
        sum(expected_hours) as expected_hours
    from {{ ref('fct_employee_capacity') }}
    group by 1, 2
),

logged as (
    select
        employee_id,
        entry_date - extract(dow from entry_date)::integer as week_start,
        sum(worked_hours) as worked_hours,
        sum(project_work_hours) as project_work_hours,
        sum(client_bd_hours) as client_bd_hours,
        sum(internal_productive_hours) as internal_productive_hours,
        sum(internal_admin_hours) as internal_admin_hours,
        sum(exempt_hours) as exempt_hours
    from {{ ref('fct_time_entries') }}
    group by 1, 2
),

weeks as (
    select
        coalesce(capacity.employee_id, logged.employee_id) as employee_id,
        coalesce(capacity.week_start, logged.week_start) as week_start,
        coalesce(capacity.standard_hours, 0) as standard_hours,
        coalesce(capacity.expected_hours, 0) as expected_hours,
        coalesce(logged.worked_hours, 0) as worked_hours,
        coalesce(logged.project_work_hours, 0) as project_work_hours,
        coalesce(logged.client_bd_hours, 0) as client_bd_hours,
        coalesce(logged.internal_productive_hours, 0) as internal_productive_hours,
        coalesce(logged.internal_admin_hours, 0) as internal_admin_hours,
        coalesce(logged.exempt_hours, 0) as exempt_hours
    from capacity
    full outer join logged
        on capacity.employee_id = logged.employee_id and capacity.week_start = logged.week_start
)

select
    weeks.employee_id::integer as employee_id,
    employees.employee_name::text as employee_name,
    employees.department_name::text as department_name,
    employees.group_name::text as group_name,
    weeks.week_start::date as week_start,
    weeks.standard_hours::numeric(10, 2) as standard_hours,
    weeks.expected_hours::numeric(10, 2) as expected_hours,
    weeks.worked_hours::numeric(10, 2) as worked_hours,
    weeks.project_work_hours::numeric(10, 2) as project_work_hours,
    weeks.client_bd_hours::numeric(10, 2) as client_bd_hours,
    weeks.internal_productive_hours::numeric(10, 2) as internal_productive_hours,
    weeks.internal_admin_hours::numeric(10, 2) as internal_admin_hours,
    weeks.exempt_hours::numeric(10, 2) as exempt_hours,
    round(weeks.worked_hours / nullif(weeks.expected_hours, 0), 4)::numeric(9, 4)
        as net_utilization_pct,
    round(weeks.project_work_hours / nullif(weeks.expected_hours, 0), 4)::numeric(9, 4)
        as project_work_pct,
    round(weeks.client_bd_hours / nullif(weeks.expected_hours, 0), 4)::numeric(9, 4)
        as client_bd_pct,
    round(
        (weeks.internal_productive_hours + weeks.internal_admin_hours)
        / nullif(weeks.expected_hours, 0),
        4
    )::numeric(9, 4) as internal_pct,
    round(weeks.exempt_hours / nullif(weeks.standard_hours, 0), 4)::numeric(9, 4) as exempt_pct
from weeks
left join {{ ref('dim_employee') }} as employees on weeks.employee_id = employees.employee_id
