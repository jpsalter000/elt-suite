-- Line items from both commerce vendors (nested in Shopfront orders, a separate
-- endpoint in Cartwheel), for live orders only, in their order's currency.
with unioned as (
    select
        'shopfront' as source_system, order_line_native_id, order_native_id, line_number,
        sku, quantity, unit_price, line_amount, is_deleted
    from {{ ref('stg_shopfront__order_lines') }}

    union all

    select
        'cartwheel', order_line_native_id, order_native_id, line_number,
        sku, quantity, unit_price, line_amount, is_deleted
    from {{ ref('stg_cartwheel__order_items') }}
)

select
    {{ vendor_key('l.source_system', 'l.order_line_native_id') }} as order_line_key,
    orders.order_key,
    l.source_system,
    orders.tenant,
    l.order_line_native_id,
    l.order_native_id,
    l.line_number,
    l.sku,
    l.quantity,
    l.unit_price,
    l.line_amount,
    orders.currency
from unioned as l
inner join {{ ref('fct_commerce_orders') }} as orders
    on orders.source_system = l.source_system
    and orders.order_native_id = l.order_native_id
where not l.is_deleted
