select
    id::integer as item_id,
    itemid as item_name,
    displayname as display_name,
    itemtype as item_type,
    {{ netsuite_flag('isinactive') }} as is_inactive
from {{ source('netsuite', 'items') }}
where _run_id = {{ latest_successful_run('items') }}
