-- The capacity spine (the workbook's "All Weeks"): one row per employee per day
-- they were employed. Expected hours are the standard day minus exempt time on
-- weekdays, never below zero, and zero at weekends.

with calendar as (
    select * from {{ ref('int_calendar') }}
),

employees as (
    select * from {{ ref('stg_netsuite__employees') }}
),

exempt as (
    select
        employee_id,
        entry_date,
        sum(exempt_hours) as exempt_hours
    from {{ ref('int_time_entries__categorized') }}
    group by employee_id, entry_date
)

select
    employees.employee_id,
    calendar.date_day,
    calendar.week_start,
    calendar.is_weekday,
    (case when calendar.is_weekday then employees.standard_hours_per_day else 0 end)
        ::numeric(8, 2) as standard_hours,
    coalesce(exempt.exempt_hours, 0)::numeric(8, 2) as exempt_hours,
    (case
        when calendar.is_weekday
            then greatest(employees.standard_hours_per_day - coalesce(exempt.exempt_hours, 0), 0)
        else 0
    end)::numeric(8, 2) as expected_hours
from employees
inner join calendar
    on
        calendar.date_day >= employees.hire_date
        and (employees.release_date is null or calendar.date_day <= employees.release_date)
left join exempt
    on
        employees.employee_id = exempt.employee_id
        and calendar.date_day = exempt.entry_date
