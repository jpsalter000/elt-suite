-- Time to Clients: hours on external clients by employee and day.

select
    entries.client_id::integer as client_id,
    clients.client_name::text as client_name,
    entries.employee_id::integer as employee_id,
    employees.employee_name::text as employee_name,
    employees.department_name::text as department_name,
    entries.entry_date::date as entry_date,
    sum(entries.hours)::numeric(10, 2) as total_hours,
    sum(entries.client_facing_hours)::numeric(10, 2) as client_facing_hours
from {{ ref('fct_time_entries') }} as entries
inner join {{ ref('dim_client') }} as clients
    on entries.client_id = clients.client_id and not clients.is_internal
left join {{ ref('dim_employee') }} as employees on entries.employee_id = employees.employee_id
group by 1, 2, 3, 4, 5, 6
