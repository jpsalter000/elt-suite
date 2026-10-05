{#- Full-refresh extracts upsert every row they read, stamping it with the run id.
    Rows a run didn't stamp were deleted in NetSuite, so staging keeps only the rows
    from the latest successful run of that job. -#}
{% macro latest_successful_run(job) -%}
(
    select run_id
    from {{ source('netsuite', '_runs') }}
    where job = '{{ job }}' and status = 'succeeded'
    order by finished_at desc
    limit 1
)
{%- endmacro %}

{#- SuiteQL renders booleans as 'T' / 'F'. -#}
{% macro netsuite_flag(column) -%}
    coalesce({{ column }} = 'T', false)
{%- endmacro %}
