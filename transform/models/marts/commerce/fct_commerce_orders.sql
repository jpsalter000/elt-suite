-- Live orders from both commerce vendors, with each vendor's status mapped to the
-- common vocabulary. Amounts are in the order's own currency (no FX conversion).
with unioned as (
    select
        'shopfront' as source_system, {{ vendor_tenant('shopfront') }} as tenant,
        order_native_id, customer_native_id, vendor_status, currency, total_amount,
        shipping_amount, discount_amount, promo_code, placed_at, updated_at, is_deleted
    from {{ ref('stg_shopfront__orders') }}

    union all

    select
        'cartwheel', {{ vendor_tenant('cartwheel') }},
        order_native_id, customer_native_id, vendor_status, currency, total_amount,
        shipping_amount, discount_amount, promo_code, placed_at, updated_at, is_deleted
    from {{ ref('stg_cartwheel__orders') }}
)

select
    {{ vendor_key('o.source_system', 'o.order_native_id') }} as order_key,
    {{ vendor_key('o.source_system', 'o.customer_native_id') }} as customer_key,
    o.source_system,
    o.tenant,
    o.order_native_id,
    o.customer_native_id,
    statuses.status,
    o.vendor_status,
    o.currency,
    o.total_amount,
    o.shipping_amount,
    o.discount_amount,
    o.promo_code,
    o.placed_at,
    o.updated_at
from unioned as o
left join {{ ref('order_status_map') }} as statuses
    on statuses.source_system = o.source_system
    and statuses.vendor_status = o.vendor_status
where not o.is_deleted
