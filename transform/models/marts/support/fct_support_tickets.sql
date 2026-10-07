-- Live tickets from both support vendors, with status and priority mapped to the
-- common vocabularies. Helpline tombstones (deleted cases) are left out.
with unioned as (
    select
        'ticketdesk' as source_system, {{ vendor_tenant('ticketdesk') }} as tenant,
        ticket_native_id, subject, vendor_status, vendor_priority, requester_email,
        requester_name, assignee_native_id, tags, time_spent_minutes, csat_positive,
        created_at, updated_at, is_deleted
    from {{ ref('stg_ticketdesk__tickets') }}

    union all

    select
        'helpline', {{ vendor_tenant('helpline') }},
        ticket_native_id, subject, vendor_status, vendor_priority, requester_email,
        requester_name, assignee_native_id, tags, time_spent_minutes, csat_positive,
        created_at, updated_at, is_deleted
    from {{ ref('stg_helpline__cases') }}
)

select
    {{ vendor_key('t.source_system', 't.ticket_native_id') }} as ticket_key,
    case
        when t.assignee_native_id is not null
            then {{ vendor_key('t.source_system', 't.assignee_native_id') }}
    end as assignee_agent_key,
    t.source_system,
    t.tenant,
    t.ticket_native_id,
    t.subject,
    statuses.status,
    priorities.priority,
    t.vendor_status,
    t.vendor_priority,
    t.requester_email,
    t.requester_name,
    t.assignee_native_id,
    t.tags,
    t.time_spent_minutes,
    t.csat_positive,
    t.created_at,
    t.updated_at
from unioned as t
left join {{ ref('ticket_status_map') }} as statuses
    on statuses.source_system = t.source_system
    and statuses.vendor_status = t.vendor_status
left join {{ ref('ticket_priority_map') }} as priorities
    on priorities.source_system = t.source_system
    and priorities.vendor_priority = t.vendor_priority
where not t.is_deleted
