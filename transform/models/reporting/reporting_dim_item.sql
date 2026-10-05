-- Published to Power BI as reporting.dim_item.
select
    item_id,
    item_name::text as item_name,
    category_code::text as category_code,
    category_name::text as category_name,
    counts_as_worked,
    is_client_facing
from {{ ref('dim_item') }}
