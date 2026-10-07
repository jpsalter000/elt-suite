select
    staff_id as agent_native_id,
    display_name as name,
    email,
    "group" as team,
    status = 'active' as is_active,
    updated_at
from {{ source('helpline', 'staff') }}
