-- Cartwheel orders: integer cents in USD/EUR/GBP, epoch seconds, '' promo codes.
select
    "orderId"::text as order_native_id,
    "customerId"::text as customer_native_id,
    "orderState" as vendor_status,
    "currencyCode" as currency,
    ("totalCents" / 100.0)::numeric(12, 2) as total_amount,
    ("shippingCents" / 100.0)::numeric(12, 2) as shipping_amount,
    ("discountCents" / 100.0)::numeric(12, 2) as discount_amount,
    nullif("promoCode", '') as promo_code,
    to_timestamp(placed) as placed_at,
    to_timestamp(modified) as updated_at,
    "isDeleted" as is_deleted
from {{ source('cartwheel', 'orders') }}
