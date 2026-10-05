-- Published to Power BI as reporting.fct_time_entries.
select
    time_entry_id,
    entry_date,
    employee_id,
    item_id,
    project_id,
    client_id,
    hours::numeric(8, 2) as hours,
    worked_hours::numeric(8, 2) as worked_hours,
    client_facing_hours::numeric(8, 2) as client_facing_hours,
    project_work_hours::numeric(8, 2) as project_work_hours,
    client_bd_hours::numeric(8, 2) as client_bd_hours,
    internal_productive_hours::numeric(8, 2) as internal_productive_hours,
    internal_admin_hours::numeric(8, 2) as internal_admin_hours,
    exempt_hours::numeric(8, 2) as exempt_hours,
    unmapped_hours::numeric(8, 2) as unmapped_hours,
    is_billable
from {{ ref('fct_time_entries') }}
