-- Customers from both commerce vendors in one shape. Soft-deleted customers stay
-- (is_deleted) so orders placed before the delete still join.
with unioned as (
    select
        'shopfront' as source_system, {{ vendor_tenant('shopfront') }} as tenant,
        customer_native_id, email, full_name, marketing_opt_in, city, country_code,
        country_name, vendor_tier, created_at, updated_at, is_deleted
    from {{ ref('stg_shopfront__customers') }}

    union all

    select
        'cartwheel', {{ vendor_tenant('cartwheel') }},
        customer_native_id, email, full_name, marketing_opt_in, city, country_code,
        country_name, vendor_tier, created_at, updated_at, is_deleted
    from {{ ref('stg_cartwheel__customers') }}
)

select
    {{ vendor_key('u.source_system', 'u.customer_native_id') }} as customer_key,
    u.source_system,
    u.tenant,
    u.customer_native_id,
    u.email,
    u.full_name,
    u.marketing_opt_in,
    u.city,
    coalesce(u.country_code, countries.country_code) as country_code,
    u.vendor_tier,
    u.created_at,
    u.updated_at,
    u.is_deleted
from unioned as u
left join {{ ref('country_codes') }} as countries
    on countries.country_name = u.country_name
