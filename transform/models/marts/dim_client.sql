select
    customer_id as client_id,
    customer_number as client_number,
    customer_name as client_name,
    is_internal,
    is_inactive
from {{ ref('stg_netsuite__customers') }}
