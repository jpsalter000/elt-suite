-- Helpline cases. Requesters are known only by email; labels are one ';'-joined
-- string, split into a JSON array like Ticketdesk's tags; effort is already in minutes; timestamps carry local offsets (timestamptz).
-- A tombstone (deleted case) keeps only its number, so every other column is null.
select
    case_number as ticket_native_id,
    title as subject,
    state as vendor_status,
    urgency as vendor_priority,
    contact ->> 'email' as requester_email,
    null::text as requester_name,
    owner ->> 'staff_id' as assignee_native_id,
    coalesce(to_jsonb(string_to_array(nullif(labels, ''), ';')), '[]'::jsonb) as tags,
    effort_minutes::integer as time_spent_minutes,
    case csat ->> 'rating'
        when 'good' then true
        when 'bad' then false
    end as csat_positive,
    opened_at as created_at,
    updated_at,
    deleted as is_deleted
from {{ source('helpline', 'cases') }}
