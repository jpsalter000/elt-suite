with projects as (
    select * from {{ ref('stg_netsuite__projects') }}
),

customers as (
    select * from {{ ref('stg_netsuite__customers') }}
),

departments as (
    select * from {{ ref('stg_netsuite__departments') }}
),

leads as (
    select * from {{ ref('stg_netsuite__employees') }}
)

select
    projects.project_id,
    projects.project_number,
    projects.project_name,
    concat_ws(' ', projects.project_number, concat_ws(' : ', customers.customer_name, projects.project_name))
        as project_display_name,
    projects.customer_id as client_id,
    customers.customer_name as client_name,
    projects.project_status,
    projects.project_status_rank,
    projects.project_status_label,
    projects.is_complete,
    projects.start_date,
    projects.projected_end_date,
    projects.department_id,
    departments.department_name,
    projects.project_manager_id,
    leads.employee_name as project_lead,
    projects.is_inactive
from projects
left join customers on projects.customer_id = customers.customer_id
left join departments on projects.department_id = departments.department_id
left join leads on projects.project_manager_id = leads.employee_id
