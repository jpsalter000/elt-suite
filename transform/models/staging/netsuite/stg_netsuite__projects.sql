-- Projects are SuiteQL "job" records. Their status is a custom list such as
-- "4. Finalized": the leading number orders the lifecycle.
select
    id::integer as project_id,
    entityid as project_number,
    companyname as project_name,
    parent::integer as customer_id,
    custentity_project_status as project_status,
    substring(custentity_project_status from '^(\d+)\.')::integer as project_status_rank,
    regexp_replace(custentity_project_status, '^\d+\.\s*', '') as project_status_label,
    -- Finalized and closed projects are priced from what was invoiced.
    coalesce(substring(custentity_project_status from '^(\d+)\.')::integer >= 4, false)
        as is_complete,
    startdate as start_date,
    projectedenddate as projected_end_date,
    department::integer as department_id,
    projectmanager::integer as project_manager_id,
    {{ netsuite_flag('isinactive') }} as is_inactive,
    lastmodifieddate as last_modified_at
from {{ source('netsuite', 'projects') }}
