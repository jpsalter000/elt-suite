select
    id::integer as account_id,
    acctnumber as account_number,
    fullname as account_name,
    accttype as account_type,
    {{ netsuite_flag('isinactive') }} as is_inactive
from {{ source('netsuite', 'accounts') }}
where _run_id = {{ latest_successful_run('accounts') }}
