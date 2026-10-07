-- Time entries are SuiteQL "timebill" records. "customer" names either a project
-- (job) or a customer; int_time_entries__categorized resolves which.
select
    id::integer as time_entry_id,
    employee::integer as employee_id,
    trandate as entry_date,
    hours::numeric(8, 2) as hours,
    customer::integer as customer_ref_id,
    item::integer as item_id,
    department::integer as department_id,
    memo,
    {{ netsuite_flag('isbillable') }} as is_billable,
    lastmodifieddate as last_modified_at
from {{ source('netsuite', 'time_entries') }}
