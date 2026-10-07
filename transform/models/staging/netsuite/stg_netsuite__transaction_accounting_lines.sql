-- NetSuite's sign convention: debits positive, credits negative.
select
    transaction::integer as transaction_id,
    transactionline::integer as line_id,
    account::integer as account_id,
    amount::numeric(14, 2) as amount,
    {{ netsuite_flag('posting') }} as is_posting
from {{ source('netsuite', 'transaction_accounting_lines') }}
where _run_id = {{ latest_successful_run('transaction_accounting_lines') }}
