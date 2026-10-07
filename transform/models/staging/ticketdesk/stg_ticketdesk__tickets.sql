-- Ticketdesk tickets. Timestamps were sent as UTC without an offset and load as
-- timestamptz in the warehouse's UTC session, so they need no conversion here.
select
    id::text as ticket_native_id,
    subject,
    status as vendor_status,
    priority as vendor_priority,
    requester ->> 'email' as requester_email,
    requester ->> 'name' as requester_name,
    assignee_id::text as assignee_native_id,
    coalesce(tags, '[]'::jsonb) as tags,
    round(time_spent_hours * 60)::integer as time_spent_minutes,
    case
        when satisfaction_score >= 4 then true
        when satisfaction_score <= 2 then false
    end as csat_positive,
    created_at,
    modified_at as updated_at,
    false as is_deleted
from {{ source('ticketdesk', 'tickets') }}
