-- Published to Power BI as reporting.dim_project.
select
    project_id,
    project_number::text as project_number,
    project_name::text as project_name,
    project_display_name::text as project_display_name,
    client_id,
    client_name::text as client_name,
    project_status::text as project_status,
    project_status_label::text as project_status_label,
    is_complete,
    start_date,
    projected_end_date,
    department_name::text as department_name,
    project_lead::text as project_lead
from {{ ref('dim_project') }}
