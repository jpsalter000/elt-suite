select
    id::integer as transaction_id,
    tranid as document_number,
    type as transaction_type,
    case type
        when 'SalesOrd' then 'Sales Order'
        when 'CustInvc' then 'Invoice'
        when 'CustCred' then 'Credit Memo'
        when 'PurchOrd' then 'Purchase Order'
        when 'VendBill' then 'Bill'
        when 'ExpRept' then 'Expense Report'
        when 'VendAuth' then 'Vendor Return Authorization'
        when 'Journal' then 'Journal'
        when 'Opprtnty' then 'Opportunity'
        else type
    end as transaction_type_name,
    status as transaction_status,
    entity::integer as entity_id,
    currency::integer as currency_id,
    foreigntotal::numeric(14, 2) as total_amount,
    memo,
    trandate as transaction_date,
    lastmodifieddate as last_modified_at
from {{ source('netsuite', 'transactions') }}
