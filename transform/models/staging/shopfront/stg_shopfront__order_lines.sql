-- Shopfront nests line items in each order; unnest them, numbering from 1 in array
-- order. Lines have no ids of their own, so the id is synthesized from the order's.
-- (The leading CTE keeps "with ordinality" from being the model's first "with", which
-- dbt's unit-test fixture injection would otherwise latch onto.)
with orders as (
    select id, line_items from {{ source('shopfront', 'orders') }}
)

select
    o.id || '-' || li.line_number as order_line_native_id,
    o.id as order_native_id,
    li.line_number::integer as line_number,
    li.item ->> 'sku' as sku,
    (li.item ->> 'quantity')::integer as quantity,
    (li.item ->> 'unit_price')::numeric(12, 2) as unit_price,
    ((li.item ->> 'quantity')::integer * (li.item ->> 'unit_price')::numeric(12, 2))::numeric(12, 2)
        as line_amount,
    false as is_deleted
from orders as o
cross join lateral jsonb_array_elements(o.line_items) with ordinality as li (item, line_number)
