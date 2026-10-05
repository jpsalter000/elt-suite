-- Published to Power BI as reporting.dim_client.
select
    client_id,
    client_number::text as client_number,
    client_name::text as client_name,
    is_internal,
    is_inactive
from {{ ref('dim_client') }}
