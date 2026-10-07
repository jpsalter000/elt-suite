-- Project transaction lines with the workbook's margin rules made explicit.
-- NetSuite records credits as negative amounts, so revenue (income and deferred
-- revenue accounts) is flipped to be positive on sales documents.
{%- set revenue_documents = "('SalesOrd', 'CustInvc', 'CustCred')" %}

with lines as (
    select * from {{ ref('stg_netsuite__transaction_lines') }}
    where not is_mainline
),

classified as (
    select
        lines.transaction_id,
        lines.line_id,
        lines.entity_id as project_id,
        projects.is_complete as project_is_complete,
        transactions.transaction_date,
        transactions.document_number,
        transactions.transaction_type,
        transactions.transaction_type_name,
        transactions.transaction_status,
        accounts.account_id,
        accounts.account_number,
        accounts.account_name,
        accounts.account_type,
        account_classes.account_class,
        (case
            when accounts.account_type in ('Income', 'OthIncome', 'DeferRevenue')
                then -accounting.amount
            else accounting.amount
        end)::numeric(14, 2) as amount,
        transactions.transaction_type in {{ revenue_documents }} as is_revenue_document,
        coalesce(
            account_classes.account_class in (
                'revenue', 'pass_through_travel', 'pass_through_incentive'
            )
            or accounts.account_type = 'DeferRevenue',
            false
        ) as is_revenue_account,
        case
            when account_classes.account_class = 'excluded'
                then 'excluded account'
            when transactions.transaction_status = 'Rejected by Supervisor'
                then 'rejected by supervisor'
            -- Billed purchase orders are counted through their bills instead.
            when
                transactions.transaction_type = 'PurchOrd'
                and transactions.transaction_status
                not in ('Pending Supervisor Approval', 'Pending Billing')
                then 'purchase order no longer pending'
            when
                accounts.account_type = 'DeferRevenue'
                and transactions.transaction_type not in {{ revenue_documents }}
                then 'deferred revenue outside a sales document'
            when transactions.transaction_type = 'VendAuth'
                then 'vendor return authorization'
        end as exclusion_reason
    from lines
    inner join {{ ref('stg_netsuite__projects') }} as projects
        on lines.entity_id = projects.project_id
    inner join {{ ref('stg_netsuite__transactions') }} as transactions
        on lines.transaction_id = transactions.transaction_id
    inner join {{ ref('stg_netsuite__transaction_accounting_lines') }} as accounting
        on lines.transaction_id = accounting.transaction_id and lines.line_id = accounting.line_id
    inner join {{ ref('stg_netsuite__accounts') }} as accounts
        on accounting.account_id = accounts.account_id
    left join {{ ref('account_classes') }} as account_classes
        on accounts.account_number = account_classes.account_number
),

priced as (
    select
        *,
        exclusion_reason is not null as is_excluded,
        -- Finalized and closed projects are priced from what was invoiced (less
        -- credits); everything else from its sales orders.
        case
            when project_is_complete then transaction_type in ('CustInvc', 'CustCred')
            else transaction_type = 'SalesOrd'
        end as counts_toward_price
    from classified
)

select
    *,
    (case
        when not is_excluded and is_revenue_document and is_revenue_account and counts_toward_price
            then amount
        else 0
    end)::numeric(14, 2) as price_amount,
    (case
        when
            not is_excluded and is_revenue_document and counts_toward_price
            and account_class in ('pass_through_travel', 'pass_through_incentive')
            then amount
        else 0
    end)::numeric(14, 2) as pass_through_amount,
    {%- for cost in ['field_cost', 'incentive_cost', 'travel_cost'] %}
    (case when not is_excluded and account_class = '{{ cost }}' then amount else 0 end)
        ::numeric(14, 2) as {{ cost }}{{ "," if not loop.last }}
    {%- endfor %}
from priced
