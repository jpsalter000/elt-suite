select
    id::text as agent_native_id,
    name,
    email,
    team,
    active as is_active,
    modified_at as updated_at
from {{ source('ticketdesk', 'agents') }}
