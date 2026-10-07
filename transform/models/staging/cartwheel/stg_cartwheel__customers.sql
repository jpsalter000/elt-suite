-- Cartwheel customers: split names, Y/N consent, '' for missing values, epoch seconds,
-- and soft deletes (kept, flagged). Country arrives as a full name; the dimension maps it.
select
    "customerId"::text as customer_native_id,
    "emailAddress" as email,
    "firstName" || ' ' || "lastName" as full_name,
    "marketingConsent" = 'Y' as marketing_opt_in,
    nullif("addrCity", '') as city,
    null::text as country_code,
    nullif("addrCountry", '') as country_name,
    nullif("loyaltyTier", '') as vendor_tier,
    to_timestamp(created) as created_at,
    to_timestamp(modified) as updated_at,
    "isDeleted" as is_deleted
from {{ source('cartwheel', 'customers') }}
