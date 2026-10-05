select
    id::integer as employee_id,
    entityid as employee_name,
    firstname as first_name,
    lastname as last_name,
    email,
    department::integer as department_id,
    title,
    {{ netsuite_flag('isinactive') }} as is_inactive,
    hiredate as hire_date,
    releasedate as release_date,
    laborcost::numeric(10, 2) as cost_per_hour,
    custentity_burdened_cost::numeric(10, 2) as burdened_cost_per_hour,
    coalesce(custentity_hours_per_day::numeric(4, 2), 8) as standard_hours_per_day,
    lastmodifieddate as last_modified_at
from {{ source('netsuite', 'employees') }}
