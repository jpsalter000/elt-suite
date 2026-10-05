# Vendor variants and normalization map

elt-suite ships four mock vendors in two domains. Each domain has one common set of record types, and each vendor delivers those records in its own shape. This page maps each vendor onto the common model. It is the specification for the dbt staging layer.

All four are separate client companies (tenants), so the common models union them. Nothing is deduplicated across vendors.

| Domain | Vendor (tenant) | Consumer | Raw tables |
| --- | --- | --- | --- |
| Commerce | Shopfront (globex) | `globex_shopfront_extract_and_load` | `globex_shopfront.customers`, `.orders` |
| Commerce | Cartwheel (umbrella) | `umbrella_cartwheel_extract_and_load` | `umbrella_cartwheel.customers`, `.orders`, `.order_items` |
| Support | Ticketdesk (initech) | `initech_ticketdesk_extract_and_load` | `initech_ticketdesk.tickets`, `.agents` |
| Support | Helpline (hooli) | `hooli_helpline_extract_and_load` | `hooli_helpline.cases`, `.staff` |

## Rules for every common model

- **Keys:** native IDs collide and differ in type (`cus_0001`, `5001`, `10001`, `HL-000001`, UUIDs). Keep the native ID as text in `<entity>_native_id` and add `source_system` and `tenant`. The key is a hash of `(source_system, native_id)`.
- **Time:** convert everything to `timestamptz` in UTC. The inputs are ISO `Z` (Shopfront), naive UTC `YYYY-MM-DD HH:MM:SS` (Ticketdesk), unix seconds (Cartwheel), and ISO with local offsets (Helpline).
- **Money:** convert to `numeric(12,2)` in major units, with an ISO currency column. Shopfront sends dollars as JSON numbers, sometimes integers, in USD only. Cartwheel sends integer cents in USD, EUR or GBP. Don't convert currencies in staging.
- **Missing values:** turn `""` into `null` (Cartwheel's `loyaltyTier`, `promoCode`, `addr*`).
- **Deletes:** every model gets `is_deleted`. Cartwheel sends `isDeleted`/`deletedAt`; the consumer requests soft-deleted rows so they arrive. Helpline sends tombstones, which replace the row, so every other column becomes null; keep attribute history with a dbt snapshot if it matters. Shopfront and Ticketdesk never delete, so use `false`.

## Commerce

### customers

| Common column | Shopfront | Cartwheel |
| --- | --- | --- |
| `customer_native_id` | `id` (`cus_0001`) | `customerId` (int) |
| `email` | `email` | `emailAddress` |
| `full_name` | `name` | `firstName \|\| ' ' \|\| lastName` |
| `marketing_opt_in` | `marketing_opt_in` (bool) | `marketingConsent = 'Y'` |
| `city` | `address->>'city'` (address may be null) | `nullif(addrCity, '')` |
| `country_code` | `address->>'country'` (ISO 3166-1 alpha-2) | `nullif(addrCountry, '')`, mapped from the full name (see below) |
| `vendor_tier` | `tier` (`free`/`pro`/`enterprise`/null) | `nullif(loyaltyTier, '')` (`BRONZE`/`SILVER`/`GOLD`) |
| `created_at` | `created_at` | `to_timestamp(created)` |
| `updated_at` | `updated_at` | `to_timestamp(modified)` |
| `is_deleted` | `false` | `isDeleted` |

Keep the two tiers as `vendor_tier` text and don't map them onto each other. Shopfront tiers are subscription plans; Cartwheel tiers are loyalty levels. They aren't the same concept.

Country names: `United States` → `US`, `Canada` → `CA`, `United Kingdom` → `GB`, `Germany` → `DE`.

### orders

| Common column | Shopfront | Cartwheel |
| --- | --- | --- |
| `order_native_id` | `id` | `orderId` |
| `customer_native_id` | `customer_id` | `customerId` |
| `status` | `status` (below) | `orderState` (below) |
| `currency` | `currency` (`USD`) | `currencyCode` |
| `total_amount` | `total` | `totalCents / 100.0` |
| `shipping_amount` | `shipping->>'cost'` | `shippingCents / 100.0` |
| `discount_amount` | not provided (null) | `discountCents / 100.0` |
| `promo_code` | `discount_code` | `nullif(promoCode, '')` |
| `placed_at` | `placed_at` | `to_timestamp(placed)` |
| `updated_at` | `updated_at` | `to_timestamp(modified)` |
| `is_deleted` | `false` | `isDeleted` |

Status vocabulary:

| Common `status` | Shopfront `status` | Cartwheel `orderState` |
| --- | --- | --- |
| `pending` | `pending` | `AWAITING_PAYMENT` |
| `paid` | `paid` | `FULFILLED` |
| `shipped` | `shipped` | `IN_TRANSIT` |
| `delivered` | `delivered` | `COMPLETE` |
| `cancelled` | `cancelled` | `VOIDED` |
| `refunded` | `refunded` | `RETURNED` |

### order_lines

Shopfront nests line items inside each order. Cartwheel serves them from their own endpoint.

| Common column | Shopfront: `jsonb_array_elements(line_items) with ordinality` | Cartwheel `order_items` |
| --- | --- | --- |
| `order_line_native_id` | `order id \|\| '-' \|\| ordinality` (synthesized) | `orderItemId` |
| `order_native_id` | parent `id` | `orderId` |
| `sku` | `sku` | `productCode` |
| `quantity` | `quantity` | `quantity` |
| `unit_price` | `unit_price` (dollars) | `unitPriceCents / 100.0` |
| `line_amount` | `quantity * unit_price` | `lineTotalCents / 100.0` |
| `currency` | parent order's `currency` | parent order's `currencyCode` |

## Support

### tickets

| Common column | Ticketdesk `tickets` | Helpline `cases` |
| --- | --- | --- |
| `ticket_native_id` | `id` (int) | `case_number` (`HL-000001`) |
| `subject` | `subject` | `title` |
| `status` | `status` (below) | `state` (below) |
| `priority` | `priority` (below) | `urgency` (below) |
| `requester_email` | `requester->>'email'` | `contact->>'email'` |
| `requester_name` | `requester->>'name'` | not provided (null) |
| `assignee_native_id` | `assignee_id` (int) | `owner->>'staff_id'` (UUID) |
| `tags` | `tags` (JSON array) | `string_to_array(nullif(labels, ''), ';')` |
| `time_spent_minutes` | `time_spent_hours * 60` | `effort_minutes` |
| `csat_positive` | `satisfaction_score >= 4`; null if unscored; a score of 3 is null | `csat->>'rating' = 'good'`; null if unrated |
| `created_at` | `created_at` read as UTC | `opened_at` (has an offset) |
| `updated_at` | `modified_at` read as UTC | `updated_at` (has an offset) |
| `is_deleted` | `false` | `deleted` (tombstone) |

| Common `status` | Ticketdesk | Helpline |
| --- | --- | --- |
| `new` | none | `new` |
| `open` | `open` | `open` |
| `pending` | `pending` | `on_hold` |
| `solved` | `solved` | `resolved` |
| `closed` | `closed` | `closed` |

| Common `priority` | Ticketdesk | Helpline |
| --- | --- | --- |
| `urgent` | `urgent` | `P1` |
| `high` | `high` | `P2` |
| `normal` | `normal` | `P3` |
| `low` | `low` | `P4` |

### agents

| Common column | Ticketdesk `agents` | Helpline `staff` |
| --- | --- | --- |
| `agent_native_id` | `id` (int) | `staff_id` (UUID) |
| `name` | `name` | `display_name` |
| `email` | `email` | `email` |
| `team` | `team` (`billing`/`technical`/`onboarding`) | `group` (`Tier 1`/`Tier 2`/`Escalations`) |
| `is_active` | `active` | `status = 'active'` |
| `updated_at` | `modified_at` read as UTC | `updated_at` |

Keep both team vocabularies as vendor values. Ticketdesk groups agents by function and Helpline by escalation tier, so neither maps onto the other.

## Extraction quirks the consumers already handle

| Vendor | Quirk | How the consumer handles it |
| --- | --- | --- |
| Shopfront | Access tokens expire partway through a sync; refresh tokens are single-use | Refresh on `token_expired` and retry; re-authenticate if the refresh is rejected |
| Ticketdesk | `modified_after` is exclusive and timestamps collide | Send `lower_bound - 1s` |
| Cartwheel | The default sort is unstable, so offset paging duplicates and skips records (4 of each across 434 orders at `limit=7`) | Always page with `sort=id` |
| Cartwheel | Soft deletes are hidden by default | `include_deleted=true` |
| Helpline | Changes commit up to 25 minutes after `updated_at` (late arrivals) | `lookback_seconds: 1800` in the consumer config |
| Helpline | Requests must be HMAC-signed within a 300s window | Sign every request at send time |
