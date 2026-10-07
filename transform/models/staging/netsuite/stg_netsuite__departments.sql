select
    id::integer as department_id,
    name as department_name,
    {{ netsuite_flag('isinactive') }} as is_inactive
from {{ source('netsuite', 'departments') }}
where _run_id = {{ latest_successful_run('departments') }}
