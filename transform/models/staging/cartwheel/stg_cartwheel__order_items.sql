-- Cartwheel serves line items from their own endpoint. They have no line number,
-- so lines are numbered within each order by item id.
select
    "orderItemId"::text as order_line_native_id,
    "orderId"::text as order_native_id,
    (row_number() over (partition by "orderId" order by "orderItemId"))::integer as line_number,
    "productCode" as sku,
    quantity::integer as quantity,
    ("unitPriceCents" / 100.0)::numeric(12, 2) as unit_price,
    ("lineTotalCents" / 100.0)::numeric(12, 2) as line_amount,
    "isDeleted" as is_deleted
from {{ source('cartwheel', 'order_items') }}
