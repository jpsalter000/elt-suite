-- Both task breakdowns ("Tasks by Person" and "Persons by Task") as monthly hours;
-- Power BI pivots them either way.

select
    date_trunc('month', entries.entry_date)::date as month_start,
    entries.employee_id::integer as employee_id,
    employees.employee_name::text as employee_name,
    employees.department_name::text as employee_department,
    entries.project_id::integer as project_id,
    projects.project_display_name::text as project_display_name,
    projects.department_name::text as project_department,
    coalesce(projects.client_name, clients.client_name)::text as client_name,
    items.item_name::text as item_name,
    items.category_name::text as category_name,
    sum(entries.hours)::numeric(10, 2) as hours
from {{ ref('fct_time_entries') }} as entries
left join {{ ref('dim_employee') }} as employees on entries.employee_id = employees.employee_id
left join {{ ref('dim_project') }} as projects on entries.project_id = projects.project_id
left join {{ ref('dim_client') }} as clients on entries.client_id = clients.client_id
left join {{ ref('dim_item') }} as items on entries.item_id = items.item_id
group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
