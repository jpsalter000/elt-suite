select
    transaction::integer as transaction_id,
    id::integer as line_id,
    linesequencenumber::integer as line_sequence,
    {{ netsuite_flag('mainline') }} as is_mainline,
    entity::integer as entity_id,
    item::integer as item_id,
    memo,
    foreignamount::numeric(14, 2) as amount
from {{ source('netsuite', 'transaction_lines') }}
where _run_id = {{ latest_successful_run('transaction_lines') }}
