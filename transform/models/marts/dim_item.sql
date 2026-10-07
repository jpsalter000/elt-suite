select
    items.item_id,
    items.item_name,
    items.display_name,
    items.item_type,
    coalesce(categories.category_code, 'unmapped') as category_code,
    coalesce(categories.category_name, 'Unmapped') as category_name,
    coalesce(categories.counts_as_worked, false) as counts_as_worked,
    coalesce(categories.is_client_facing, false) as is_client_facing,
    items.is_inactive
from {{ ref('stg_netsuite__items') }} as items
left join {{ ref('time_categories') }} as categories
    on lower(items.item_name) = lower(categories.item_name)
