select
    id::integer as customer_id,
    entityid as customer_number,
    companyname as customer_name,
    email,
    phone,
    {{ netsuite_flag('isinactive') }} as is_inactive,
    companyname in (
        {%- for name in var('internal_client_names') %}'{{ name }}'{{ ", " if not loop.last }}{% endfor -%}
    ) as is_internal,
    datecreated as created_at,
    lastmodifieddate as last_modified_at
from {{ source('netsuite', 'customers') }}
