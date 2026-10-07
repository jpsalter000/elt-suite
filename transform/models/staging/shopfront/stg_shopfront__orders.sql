-- Shopfront orders: dollars arrive as JSON numbers (sometimes integers), USD only.
-- Shopfront reports no discount amount, only the code.
select
    id as order_native_id,
    customer_id as customer_native_id,
    status as vendor_status,
    currency,
    round(total::numeric, 2)::numeric(12, 2) as total_amount,
    (shipping ->> 'cost')::numeric(12, 2) as shipping_amount,
    null::numeric(12, 2) as discount_amount,
    discount_code as promo_code,
    placed_at,
    updated_at,
    false as is_deleted
from {{ source('shopfront', 'orders') }}
