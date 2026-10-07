-- External clients ranked by client-facing hours. This replaces the workbook's
-- "Client Sheet" pivot and the GetTop15 macro.

with totals as (
    select
        entries.client_id,
        clients.client_name,
        sum(entries.client_facing_hours) as client_facing_hours,
        sum(entries.hours) as total_hours
    from {{ ref('fct_time_entries') }} as entries
    inner join {{ ref('dim_client') }} as clients
        on entries.client_id = clients.client_id and not clients.is_internal
    group by 1, 2
),

ranked as (
    select
        *,
        rank() over (order by client_facing_hours desc, total_hours desc) as client_rank
    from totals
)

select
    client_id::integer as client_id,
    client_name::text as client_name,
    client_facing_hours::numeric(10, 2) as client_facing_hours,
    total_hours::numeric(10, 2) as total_hours,
    client_rank::integer as client_rank,
    client_rank <= {{ var('top_clients') }} as is_top_15
from ranked
