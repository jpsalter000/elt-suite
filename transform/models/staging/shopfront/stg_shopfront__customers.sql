-- Shopfront customers. The address object is optional; its country is already ISO alpha-2.
select
    id as customer_native_id,
    email,
    name as full_name,
    marketing_opt_in,
    address ->> 'city' as city,
    address ->> 'country' as country_code,
    null::text as country_name,
    tier as vendor_tier,
    created_at,
    updated_at,
    false as is_deleted
from {{ source('shopfront', 'customers') }}
