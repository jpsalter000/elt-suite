-- Agents from both support vendors. Teams stay in each vendor's own vocabulary:
-- Ticketdesk groups by function, Helpline by escalation tier.
with unioned as (
    select
        'ticketdesk' as source_system, {{ vendor_tenant('ticketdesk') }} as tenant,
        agent_native_id, name, email, team, is_active, updated_at
    from {{ ref('stg_ticketdesk__agents') }}

    union all

    select
        'helpline', {{ vendor_tenant('helpline') }},
        agent_native_id, name, email, team, is_active, updated_at
    from {{ ref('stg_helpline__staff') }}
)

select
    {{ vendor_key('source_system', 'agent_native_id') }} as agent_key,
    source_system,
    tenant,
    agent_native_id,
    name,
    email,
    team,
    is_active,
    updated_at
from unioned
